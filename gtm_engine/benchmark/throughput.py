import time
from typing import Dict
from gtm_engine.queues.queue_provider import queue_provider

def run_throughput_benchmark(num_tasks: int = 100) -> Dict[str, float]:
    """Measures task enqueue and dequeue throughput per second."""
    queue = queue_provider.get_queue()
    
    # Enqueue benchmark
    start_enqueue = time.perf_counter()
    for i in range(num_tasks):
        queue.enqueue(
            lead_id=i,
            task={"type": "email", "to": f"user{i}@example.com", "subject": "Test", "body": "Hello"},
            priority=5,
            idempotency_key=f"bench_key_{i}"
        )
    end_enqueue = time.perf_counter()
    
    enqueue_duration = end_enqueue - start_enqueue
    enqueue_throughput = num_tasks / enqueue_duration
    
    # Dequeue benchmark
    start_dequeue = time.perf_counter()
    for i in range(num_tasks):
        task = queue.dequeue()
        if task:
            queue.ack(task["queue_id"], {"status": "success"})
    end_dequeue = time.perf_counter()
    
    dequeue_duration = end_dequeue - start_dequeue
    dequeue_throughput = num_tasks / dequeue_duration
    
    return {
        "enqueue_duration_sec": enqueue_duration,
        "enqueue_throughput_tasks_per_sec": enqueue_throughput,
        "dequeue_duration_sec": dequeue_duration,
        "dequeue_throughput_tasks_per_sec": dequeue_throughput
    }
