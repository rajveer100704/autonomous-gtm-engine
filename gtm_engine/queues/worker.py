from __future__ import annotations

import logging
import time

log = logging.getLogger("gtm.queues.worker")


class Worker:
    """Processes all pending queue tasks synchronously."""

    def process_all(self, max_tasks: int = 500, worker_id: str = "default_worker") -> list[dict]:
        from gtm_engine.queues.queue_provider import queue_provider
        from gtm_engine.queues.dead_letter_queue import dead_letter_queue
        from gtm_engine.utils.circuit_breaker import get_circuit, CircuitOpenError
        from gtm_engine.audit.audit_log import audit_log

        queue = queue_provider.get_queue()
        results = []
        processed = 0

        while processed < max_tasks:
            task = queue.dequeue(worker_id=worker_id)
            if task is None:
                break

            queue_id    = task["queue_id"]
            lead_id     = task.get("lead_id")
            task_type   = task.get("type", "unknown")
            retry_count = task.get("retry_count", 0)
            max_retries = task.get("max_retries", 3)
            ikey        = task.get("idempotency_key")

            with audit_log.span(
                "worker", f"execute_{task_type}",
                reason=f"queue_id={queue_id} retry={retry_count}",
                lead_id=lead_id,
                inputs={"type": task_type, "queue_id": queue_id},
            ) as span:
                try:
                    # ── Circuit breaker check ──────────────────────────────────
                    service_map = {"email": "gmail", "linkedin": "linkedin"}
                    service = service_map.get(task_type, task_type)
                    cb = get_circuit(service, failure_threshold=5, recovery_timeout=60)

                    with cb:
                        result = self._dispatch(task)

                    queue.ack(queue_id, result)
                    span.set_outputs(result)
                    results.append({"queue_id": queue_id, "success": True, **result})
                    log.info("worker: completed task %d (type=%s lead=%s)", queue_id, task_type, lead_id)

                    # Prometheus metrics tracking
                    try:
                        from gtm_engine.observability.metrics import TASK_PROCESSED_COUNTER, CIRCUIT_BREAKER_STATE_GAUGE
                        TASK_PROCESSED_COUNTER.labels(type=task_type, status="success").inc()
                        CIRCUIT_BREAKER_STATE_GAUGE.labels(service=service).set(0) # 0 = closed
                    except Exception:
                        pass

                    # Emit Domain Event
                    from gtm_engine.events.event_bus import bus, EventType
                    et = EventType.EMAIL_SENT if task_type == "email" else EventType.LINKEDIN_SENT
                    try:
                        bus.emit(
                            et,
                            lead_id=lead_id,
                            outreach_id=task.get("outreach_id"),
                            subject=task.get("subject"),
                            **result
                        )
                    except Exception as ev_exc:
                        log.error("worker: failed to emit send event: %s", ev_exc)

                except CircuitOpenError as exc:
                    # Circuit is OPEN — don't retry yet, leave as failed
                    log.warning("worker: circuit open for %s — skipping task %d", exc.service, queue_id)
                    queue.nack(queue_id, f"CircuitOpen: {exc}")
                    results.append({"queue_id": queue_id, "success": False, "error": str(exc), "circuit_open": True})
                    span.set_error(str(exc))

                    try:
                        from gtm_engine.observability.metrics import TASK_PROCESSED_COUNTER, CIRCUIT_BREAKER_STATE_GAUGE
                        TASK_PROCESSED_COUNTER.labels(type=task_type, status="circuit_open").inc()
                        CIRCUIT_BREAKER_STATE_GAUGE.labels(service=exc.service).set(1) # 1 = open
                    except Exception:
                        pass

                except Exception as exc:
                    error_str = str(exc)
                    log.warning(
                         "worker: task %d failed (retry %d/%d): %s",
                         queue_id, retry_count + 1, max_retries, error_str,
                    )
                    queue.nack(queue_id, error_str)

                    # Prometheus metrics tracking
                    try:
                        from gtm_engine.observability.metrics import TASK_PROCESSED_COUNTER, CIRCUIT_BREAKER_STATE_GAUGE
                        TASK_PROCESSED_COUNTER.labels(type=task_type, status="failed").inc()
                        state_val = 1 if cb.state == "open" else (2 if cb.state == "half-open" else 0)
                        CIRCUIT_BREAKER_STATE_GAUGE.labels(service=service).set(state_val)
                    except Exception:
                        pass

                    # Emit Failure Domain Event
                    from gtm_engine.events.event_bus import bus, EventType
                    et = EventType.EMAIL_FAILED if task_type == "email" else EventType.LINKEDIN_FAILED
                    try:
                        bus.emit(
                            et,
                            lead_id=lead_id,
                            outreach_id=task.get("outreach_id"),
                            error=error_str
                        )
                    except Exception as ev_exc:
                        log.error("worker: failed to emit fail event: %s", ev_exc)

                    if retry_count + 1 >= max_retries:
                        # Move to DLQ
                        dlq_id = dead_letter_queue.add(
                            lead_id=lead_id,
                            task=task,
                            failure_reason=error_str,
                            retry_count=retry_count + 1,
                            idempotency_key=ikey,
                        )
                        log.error("worker: task %d → DLQ (id=%d) after %d retries", queue_id, dlq_id, max_retries)
                        results.append({
                            "queue_id": queue_id, "success": False,
                            "error": error_str, "dlq_id": dlq_id,
                        })
                    else:
                        # Reset to pending for retry
                        time.sleep(min(2 ** retry_count, 30))  # simple backoff
                        queue.retry(queue_id)
                        results.append({
                            "queue_id": queue_id, "success": False,
                            "error": error_str, "retrying": True,
                        })
                    span.set_error(error_str)

            processed += 1

        # Escalate any exhausted tasks to DLQ (from prior worker runs)
        self._escalate_exhausted()
        return results

    def _dispatch(self, task: dict) -> dict:
        task_type = task.get("type")
        if task_type == "email":
            return self._run_email(task)
        elif task_type == "linkedin":
            return self._run_linkedin(task)
        else:
            raise ValueError(f"Unknown task type: {task_type!r}")

    def _run_email(self, task: dict) -> dict:
        from gtm_engine.executors.gmail_executor import send_email
        return send_email(
            to      = task.get("to", ""),
            subject = task.get("subject", ""),
            body    = task.get("body", ""),
            lead_id = task.get("lead_id"),
        )

    def _run_linkedin(self, task: dict) -> dict:
        import asyncio
        from gtm_engine.executors.linkedin_executor import LinkedInExecutor
        executor = LinkedInExecutor()
        return asyncio.run(executor.send_connection_request(
            profile_url = task.get("profile_url", ""),
            message     = task.get("message", ""),
        ))

    def _escalate_exhausted(self) -> None:
        """Move any previously-failed tasks that hit max_retries into DLQ."""
        from gtm_engine.queues.queue_provider import queue_provider
        from gtm_engine.queues.dead_letter_queue import dead_letter_queue

        queue = queue_provider.get_queue()
        exhausted = queue.get_failed_ready_for_dlq()
        for task in exhausted:
            dead_letter_queue.add(
                lead_id=task.get("lead_id"),
                task=task,
                failure_reason=task.get("error", "unknown"),
                retry_count=task.get("retry_count", 0),
                idempotency_key=task.get("idempotency_key"),
            )
            # Set queue status to 'dlq' so we don't re-escalate
            # Use appropriate SQL execution depending on adapter
            from gtm_engine.queues.queue_adapters import SQLiteQueueAdapter
            if isinstance(queue, SQLiteQueueAdapter):
                from sqlalchemy import text
                from gtm_engine.crm.db import engine
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE execution_queue SET status='dlq' WHERE id=:id"),
                        {"id": task["queue_id"]},
                    )
            else:
                # Redis adapter
                queue.r.hset(f"gtm_task:{task['queue_id']}", "status", "dlq")


if __name__ == "__main__":
    import os
    import time
    worker = Worker()
    w_id = os.environ.get("WORKER_ID", "default_worker")
    log.info("Worker process starting with ID: %s", w_id)
    while True:
        try:
            worker.process_all(worker_id=w_id)
        except Exception as e:
            log.error("Worker loop exception: %s", e)
        time.sleep(1)
