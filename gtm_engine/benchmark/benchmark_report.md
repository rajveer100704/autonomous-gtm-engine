# GTM Engine V4.2 Performance Benchmark Report

Generated on: 2026-07-17 14:02:45
Active Queue Adapter: **SQLiteQueueAdapter**

---

## 🚀 Throughput Profile
Measures tasks completed per second under standard workload pressure.

| Action | Total Tasks | Duration (seconds) | Throughput (tasks/sec) |
| :--- | :--- | :--- | :--- |
| **Enqueue** | 100 | 0.0559 | **1789.57** |
| **Dequeue + Ack** | 100 | 0.0989 | **1010.97** |

---

## ⏱️ Latency Percentiles
Detailed latency profiles measured in milliseconds.

| Operation | Average (mean) | p50 (Median) | p95 Percentile | p99 (Tail Latency) |
| :--- | :--- | :--- | :--- | :--- |
| **Enqueue** | 0.67 ms | 0.60 ms | 1.07 ms | 1.64 ms |
| **Dequeue** | 0.55 ms | 0.54 ms | 0.71 ms | 0.96 ms |
| **Acknowledge (Ack)** | 0.00 ms | 0.00 ms | 0.00 ms | 0.00 ms |

---

## 📈 Analysis & Recommendations
- **SQLite Latency:** SQLite provides sub-millisecond execution times for local development, making it highly optimal for fast test suites.
- **Production Scalability:** Switch to the `RedisQueueAdapter` in multi-worker environments to prevent SQLite write-locking and scale horizontally.
