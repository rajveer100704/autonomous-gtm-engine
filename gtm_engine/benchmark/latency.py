import time
import numpy as np
from typing import Dict, List
from gtm_engine.queues.queue_provider import queue_provider

def run_latency_benchmark(num_tasks: int = 100) -> Dict[str, Dict[str, float]]:
    """Measures latency percentile profiles (p50, p95, p99) for core queue actions."""
    queue = queue_provider.get_queue()
    
    enqueue_latencies: List[float] = []
    dequeue_latencies: List[float] = []
    ack_latencies: List[float] = []
    
    # Measure enqueue latencies
    for i in range(num_tasks):
        start = time.perf_counter()
        qid = queue.enqueue(
            lead_id=i,
            task={"type": "email", "to": f"user{i}@example.com", "subject": "Test", "body": "Hello"},
            priority=5,
            idempotency_key=f"latency_key_{i}"
        )
        enqueue_latencies.append((time.perf_counter() - start) * 1000) # milliseconds
        
    # Measure dequeue & ack latencies
    for i in range(num_tasks):
        start_deq = time.perf_counter()
        task = queue.dequeue()
        dequeue_latencies.append((time.perf_counter() - start_deq) * 1000)
        
        if task:
            start_ack = time.perf_counter()
            queue.ack(task["queue_id"], {"status": "success"})
            ack_latencies.append((time.perf_counter() - start_ack) * 1000)
            
    def get_percentiles(latencies: List[float]) -> Dict[str, float]:
        if not latencies:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0}
        arr = np.array(latencies)
        return {
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
            "mean": float(np.mean(arr))
        }

    return {
        "enqueue": get_percentiles(enqueue_latencies),
        "dequeue": get_percentiles(dequeue_latencies),
        "ack": get_percentiles(ack_latencies)
    }
