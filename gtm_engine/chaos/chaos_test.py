import os
import time
import logging
from gtm_engine.queues.queue_provider import queue_provider
from gtm_engine.queues.worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gtm.chaos")

class ChaosSimulator:
    def __init__(self):
        self.queue = queue_provider.get_queue()

    def run_all(self):
        log.info("=== Starting V4.2 Chaos Testing & Recovery Validation ===")
        self.test_worker_crash_recovery()
        self.test_idempotency_prevents_duplicate_sends()
        log.info("=== Chaos Testing Completed Successfully: System Resilient! ===")

    def test_worker_crash_recovery(self):
        log.info("[Chaos] Stage 1: Worker crash recovery test...")
        
        # Enqueue a task
        task_id = self.queue.enqueue(
            lead_id=999,
            task={"type": "email", "to": "chaos@example.com", "subject": "Chaos Test", "body": "Resilience check"},
            priority=5,
            idempotency_key="chaos_key_1"
        )
        
        # Dequeue to simulate Worker 1 picking it up
        task = self.queue.dequeue(worker_id="worker_1")
        assert task is not None
        assert task["queue_id"] == task_id
        
        # Verify status is 'processing'
        peeked = self.queue.peek(task_id)
        assert peeked["status"] == "processing"
        log.info("[Chaos] Task %d is currently 'processing' by worker_1", task_id)
        
        # Simulate worker_1 crashing (dying) without acking.
        # Worker 2 boots up and claims/retries the task.
        log.warning("[Chaos] SIMULATED CRASH: worker_1 terminates unexpectedly!")
        
        # Nack the task to simulate failure detection/timeout recovery
        self.queue.nack(task_id, "WorkerCrash: connection lost")
        
        # Reset/Retry
        self.queue.retry(task_id)
        log.info("[Chaos] Task %d successfully reset to 'pending' state.", task_id)
        
        # Dequeue to simulate Worker 2 picking it up
        task_recovered = self.queue.dequeue(worker_id="worker_2")
        assert task_recovered is not None
        assert task_recovered["queue_id"] == task_id
        assert task_recovered["retry_count"] == 1
        log.info("[Chaos] RECOVERY SUCCESS: worker_2 picked up task %d (retry count: %d)", task_id, task_recovered["retry_count"])
        
        # Ack it
        self.queue.ack(task_id, {"status": "success"})
        log.info("[Chaos] worker_2 completed task %d successfully.", task_id)

    def test_idempotency_prevents_duplicate_sends(self):
        log.info("[Chaos] Stage 2: Idempotency safety under duplicate enqueue triggers...")
        
        key = "idempotent_chaos_key"
        
        # First enqueue
        id1 = self.queue.enqueue(
            lead_id=999,
            task={"type": "email", "to": "duplicate@example.com", "subject": "Test", "body": "Unique body"},
            idempotency_key=key
        )
        
        # Duplicate enqueue (simulate retried network trigger)
        id2 = self.queue.enqueue(
            lead_id=999,
            task={"type": "email", "to": "duplicate@example.com", "subject": "Test", "body": "Unique body"},
            idempotency_key=key
        )
        
        # They should return the exact same queue ID
        assert id1 == id2
        log.info("[Chaos] IDEMPOTENCY SUCCESS: duplicate enqueue blocked, matched existing task ID %d", id1)
        
        # Clean up
        self.queue.ack(id1, {"status": "success"})


if __name__ == "__main__":
    sim = ChaosSimulator()
    sim.run_all()
