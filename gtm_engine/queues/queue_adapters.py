import os
import json
import datetime
import logging
import hashlib
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from gtm_engine.crm import db
metadata = db.metadata

log = logging.getLogger("gtm.queues.adapters")

_MAX_RETRIES_DEFAULT = 3


class BaseQueueAdapter(ABC):
    @staticmethod
    def make_key(
        lead_id: int,
        channel: str,
        step: int = 0,
        template_version: str = "default",
        campaign_id: str = "default",
    ) -> str:
        """Hash-based idempotency key."""
        raw = f"{campaign_id}:{lead_id}:{channel}:{step}:{template_version}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @abstractmethod
    def enqueue(self, lead_id: int, task: dict, priority: int = 5, idempotency_key: str = None, max_retries: int = 3) -> int:
        """Add a task to the queue. Returns unique integer queue ID."""
        pass

    @abstractmethod
    def dequeue(self, worker_id: str = "default_worker") -> Optional[dict]:
        """Pop the next pending task. Sets status to 'processing'."""
        pass

    @abstractmethod
    def ack(self, queue_id: int, result: dict) -> None:
        """Mark task as successfully completed."""
        pass

    @abstractmethod
    def nack(self, queue_id: int, error: str) -> None:
        """Mark task execution as failed (increments retry count)."""
        pass

    @abstractmethod
    def retry(self, queue_id: int) -> None:
        """Reset task status to 'pending' to trigger a retry."""
        pass

    @abstractmethod
    def peek(self, queue_id: int) -> Optional[dict]:
        """Inspect a task's state/payload without dequeuing it."""
        pass

    @abstractmethod
    def stats(self) -> dict:
        """Get counts of tasks grouped by status."""
        pass

    @abstractmethod
    def pending_count(self) -> int:
        """Get count of pending tasks."""
        pass

    @abstractmethod
    def get_failed_ready_for_dlq(self) -> List[dict]:
        """Return tasks that have exhausted max_retries."""
        pass

    @abstractmethod
    def complete(self, queue_id: int, result: dict) -> None:
        """Alias for ack() to maintain backward compatibility with V3."""
        pass

    @abstractmethod
    def fail(self, queue_id: int, error: str) -> None:
        """Alias for nack() to maintain backward compatibility with V3."""
        pass


# ── SQLite Queue Adapter ───────────────────────────────────────────────────

class SQLiteQueueAdapter(BaseQueueAdapter):
    """
    SQLite-backed queue adapter, matching standard CRM schema.
    """
    def __init__(self):
        self._ensure_table()

    def _ensure_table(self) -> None:
        from sqlalchemy import Table, Column, Integer, Text, DateTime, String, inspect as sa_inspect
        insp = sa_inspect(db.engine)
        if "execution_queue" not in insp.get_table_names():
            Table(
                "execution_queue", metadata,
                Column("id",               Integer, primary_key=True, autoincrement=True),
                Column("lead_id",          Integer),
                Column("task_json",        Text),
                Column("status",           String(32), default="pending"),
                Column("priority",         Integer, default=5),
                Column("idempotency_key",  String(128), nullable=True),
                Column("retry_count",      Integer, default=0),
                Column("max_retries",      Integer, default=_MAX_RETRIES_DEFAULT),
                Column("result_json",      Text,    nullable=True),
                Column("error",            Text,    nullable=True),
                Column("created_at",       DateTime),
                Column("updated_at",       DateTime),
                extend_existing=True,
            ).create(db.engine)
            log.info("SQLiteQueueAdapter: created execution_queue table")

    def enqueue(self, lead_id: int, task: dict, priority: int = 5, idempotency_key: str = None, max_retries: int = 3) -> int:
        now = datetime.datetime.now(datetime.timezone.utc)
        if idempotency_key:
            with db.engine.connect() as conn:
                existing = conn.execute(
                    text("SELECT id FROM execution_queue WHERE idempotency_key = :key LIMIT 1"),
                    {"key": idempotency_key},
                ).fetchone()
            if existing:
                return existing[0]

        with db.engine.begin() as conn:
            result = conn.execute(
                text("""INSERT INTO execution_queue
                    (lead_id, task_json, status, priority, idempotency_key,
                     retry_count, max_retries, created_at, updated_at)
                    VALUES (:lead_id, :task_json, 'pending', :priority, :ikey,
                             0, :max_retries, :now, :now)"""),
                {
                    "lead_id": lead_id,
                    "task_json": json.dumps(task),
                    "priority": priority,
                    "ikey": idempotency_key,
                    "max_retries": max_retries,
                    "now": now,
                },
            )
            return result.lastrowid

    def dequeue(self, worker_id: str = "default_worker") -> Optional[dict]:
        now = datetime.datetime.now(datetime.timezone.utc)
        with db.engine.begin() as conn:
            row = conn.execute(
                text("""SELECT id, lead_id, task_json, retry_count, max_retries,
                              idempotency_key
                         FROM execution_queue
                        WHERE status='pending'
                        ORDER BY priority ASC, created_at ASC
                        LIMIT 1""")
            ).fetchone()

            if not row:
                return None

            conn.execute(
                text("UPDATE execution_queue SET status='processing', updated_at=:now WHERE id=:id"),
                {"now": now, "id": row[0]},
            )

        task = json.loads(row[2])
        task["queue_id"]        = row[0]
        task["lead_id"]         = row[1]
        task["retry_count"]     = row[3]
        task["max_retries"]     = row[4]
        task["idempotency_key"] = row[5]
        return task

    def ack(self, queue_id: int, result: dict) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        with db.engine.begin() as conn:
            conn.execute(
                text("""UPDATE execution_queue
                    SET status='completed', result_json=:result, updated_at=:now
                    WHERE id=:id"""),
                {"result": json.dumps(result), "now": now, "id": queue_id},
            )

    def nack(self, queue_id: int, error: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        with db.engine.begin() as conn:
            conn.execute(
                text("""UPDATE execution_queue
                    SET status='failed', error=:error,
                        retry_count = retry_count + 1,
                        updated_at=:now
                    WHERE id=:id"""),
                {"error": error, "now": now, "id": queue_id},
            )

    def retry(self, queue_id: int) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        with db.engine.begin() as conn:
            conn.execute(
                text("UPDATE execution_queue SET status='pending', updated_at=:now WHERE id=:id"),
                {"now": now, "id": queue_id},
            )

    def peek(self, queue_id: int) -> Optional[dict]:
        with db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT lead_id, task_json, status, error, result_json FROM execution_queue WHERE id=:id"),
                {"id": queue_id}
            ).fetchone()
        if not row:
            return None
        t = json.loads(row[1])
        t.update({
            "queue_id": queue_id,
            "lead_id": row[0],
            "status": row[2],
            "error": row[3],
            "result": json.loads(row[4]) if row[4] else None
        })
        return t

    def pending_count(self) -> int:
        with db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) FROM execution_queue WHERE status='pending'")
            ).fetchone()
        return row[0] if row else 0

    def stats(self) -> dict:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT status, COUNT(*) FROM execution_queue GROUP BY status")
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def get_failed_ready_for_dlq(self) -> List[dict]:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("""SELECT id, lead_id, task_json, retry_count, max_retries, error, idempotency_key
                        FROM execution_queue
                        WHERE status='failed' AND retry_count >= max_retries""")
            ).fetchall()
        result = []
        for r in rows:
            t = json.loads(r[2])
            t.update({
                "queue_id": r[0], "lead_id": r[1],
                "retry_count": r[3], "max_retries": r[4],
                "error": r[5], "idempotency_key": r[6],
            })
            result.append(t)
        return result

    def complete(self, queue_id: int, result: dict) -> None:
        self.ack(queue_id, result)

    def fail(self, queue_id: int, error: str) -> None:
        self.nack(queue_id, error)


# ── Redis Queue Adapter ────────────────────────────────────────────────────

class RedisQueueAdapter(BaseQueueAdapter):
    """
    Redis Streams-backed distributed queue adapter.
    """
    def __init__(self, redis_url: str):
        import redis
        self.redis_url = redis_url
        self.r = redis.Redis.from_url(redis_url, decode_responses=True)
        self.r.ping()
        self.stream_name = "gtm_stream"
        self.group_name = "gtm_group"
        
        # Ensure consumer group exists
        try:
            self.r.xgroup_create(self.stream_name, self.group_name, id="0", mkstream=True)
        except Exception:
            pass # Group already exists

    def enqueue(self, lead_id: int, task: dict, priority: int = 5, idempotency_key: str = None, max_retries: int = 3) -> int:
        # Idempotency check
        if idempotency_key:
            existing_id = self.r.hget("gtm_idempotency_keys", idempotency_key)
            if existing_id:
                return int(existing_id)

        # Generate unique integer queue_id
        queue_id = self.r.incr("gtm_queue_id_seq")
        if idempotency_key:
            self.r.hset("gtm_idempotency_keys", idempotency_key, str(queue_id))

        # Store task state metadata in Redis Hash
        task_meta = {
            "queue_id": queue_id,
            "lead_id": lead_id,
            "task_json": json.dumps(task),
            "status": "pending",
            "priority": priority,
            "idempotency_key": idempotency_key or "",
            "retry_count": 0,
            "max_retries": max_retries,
            "error": "",
            "result_json": ""
        }
        self.r.hset(f"gtm_task:{queue_id}", mapping=task_meta)
        self.r.sadd("gtm_task_ids", str(queue_id))

        # Push integer reference ID into the Redis Stream
        stream_payload = {
            "queue_id": str(queue_id),
            "priority": str(priority)
        }
        # Add to stream
        msg_id = self.r.xadd(self.stream_name, stream_payload)
        # Store message ID mapping to allow acknowledgement later
        self.r.hset("gtm_stream_msg_ids", str(queue_id), msg_id)
        return queue_id

    def dequeue(self, worker_id: str = "default_worker") -> Optional[dict]:
        # Read next message from the stream group
        try:
            # First, try to read owned pending messages (PEL recovery)
            pending = self.r.xreadgroup(self.group_name, worker_id, {self.stream_name: "0"}, count=1)
            if not pending or not pending[0][1]:
                # No pending, read new messages
                pending = self.r.xreadgroup(self.group_name, worker_id, {self.stream_name: ">"}, count=1)
                
            if not pending or not pending[0][1]:
                return None
                
            stream_data = pending[0][1][0]
            msg_id = stream_data[0]
            fields = stream_data[1]
            queue_id = int(fields["queue_id"])
            
            # Fetch full task metadata
            meta = self.r.hgetall(f"gtm_task:{queue_id}")
            if not meta:
                # Task meta was deleted, ack to clean stream
                self.r.xack(self.stream_name, self.group_name, msg_id)
                return self.dequeue(worker_id)

            # Update status
            self.r.hset(f"gtm_task:{queue_id}", "status", "processing")
            
            task = json.loads(meta["task_json"])
            task["queue_id"]        = queue_id
            task["lead_id"]         = int(meta["lead_id"])
            task["retry_count"]     = int(meta["retry_count"])
            task["max_retries"]     = int(meta["max_retries"])
            task["idempotency_key"] = meta.get("idempotency_key")
            return task
        except Exception as e:
            log.error("RedisQueueAdapter: dequeue error: %s", e)
            return None

    def ack(self, queue_id: int, result: dict) -> None:
        self.r.hset(f"gtm_task:{queue_id}", mapping={
            "status": "completed",
            "result_json": json.dumps(result)
        })
        msg_id = self.r.hget("gtm_stream_msg_ids", str(queue_id))
        if msg_id:
            self.r.xack(self.stream_name, self.group_name, msg_id)

    def nack(self, queue_id: int, error: str) -> None:
        retries = self.r.hincrby(f"gtm_task:{queue_id}", "retry_count", 1)
        self.r.hset(f"gtm_task:{queue_id}", mapping={
            "status": "failed",
            "error": error
        })
        
        # Check if exhausted
        max_retries = int(self.r.hget(f"gtm_task:{queue_id}", "max_retries") or 3)
        if retries >= max_retries:
            # We leave it as failed so workers won't pull again, DLQ scanner will move it
            msg_id = self.r.hget("gtm_stream_msg_ids", str(queue_id))
            if msg_id:
                self.r.xack(self.stream_name, self.group_name, msg_id)

    def retry(self, queue_id: int) -> None:
        self.r.hset(f"gtm_task:{queue_id}", "status", "pending")
        # To retry, we xack the old stream message and push a new one to the stream
        msg_id = self.r.hget("gtm_stream_msg_ids", str(queue_id))
        if msg_id:
            try:
                self.r.xack(self.stream_name, self.group_name, msg_id)
                self.r.xdel(self.stream_name, msg_id)
            except Exception:
                pass
        
        priority = self.r.hget(f"gtm_task:{queue_id}", "priority") or "5"
        new_msg_id = self.r.xadd(self.stream_name, {
            "queue_id": str(queue_id),
            "priority": str(priority)
        })
        self.r.hset("gtm_stream_msg_ids", str(queue_id), new_msg_id)

    def peek(self, queue_id: int) -> Optional[dict]:
        meta = self.r.hgetall(f"gtm_task:{queue_id}")
        if not meta:
            return None
        t = json.loads(meta["task_json"])
        t.update({
            "queue_id": queue_id,
            "lead_id": int(meta["lead_id"]),
            "status": meta["status"],
            "error": meta.get("error"),
            "result": json.loads(meta["result_json"]) if meta.get("result_json") else None
        })
        return t

    def pending_count(self) -> int:
        count = 0
        task_ids = self.r.smembers("gtm_task_ids")
        for tid in task_ids:
            status = self.r.hget(f"gtm_task:{tid}", "status")
            if status == "pending":
                count += 1
        return count

    def stats(self) -> dict:
        counts = {}
        task_ids = self.r.smembers("gtm_task_ids")
        for tid in task_ids:
            status = self.r.hget(f"gtm_task:{tid}", "status")
            if status:
                counts[status] = counts.get(status, 0) + 1
        return counts

    def get_failed_ready_for_dlq(self) -> List[dict]:
        failures = []
        task_ids = self.r.smembers("gtm_task_ids")
        for tid in task_ids:
            meta = self.r.hgetall(f"gtm_task:{tid}")
            if meta and meta.get("status") == "failed":
                retries = int(meta.get("retry_count", 0))
                max_r = int(meta.get("max_retries", 3))
                if retries >= max_r:
                    t = json.loads(meta["task_json"])
                    t.update({
                        "queue_id": int(tid),
                        "lead_id": int(meta["lead_id"]),
                        "retry_count": retries,
                        "max_retries": max_r,
                        "error": meta.get("error", ""),
                        "idempotency_key": meta.get("idempotency_key"),
                    })
                    failures.append(t)
        return failures

    def complete(self, queue_id: int, result: dict) -> None:
        self.ack(queue_id, result)

    def fail(self, queue_id: int, error: str) -> None:
        self.nack(queue_id, error)
