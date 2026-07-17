import os
import csv
import datetime
from gtm_engine.queues.queue_provider import queue_provider
from gtm_engine.benchmark.throughput import run_throughput_benchmark
from gtm_engine.benchmark.latency import run_latency_benchmark

def main():
    print("=== Running GTM Engine V4.2 Queue Benchmarks ===")
    
    # Resolve queue adapter name
    queue = queue_provider.get_queue()
    adapter_name = queue.__class__.__name__
    print(f"Active Adapter: {adapter_name}")
    
    # Run benchmarks
    num_tasks = 100
    tp_stats = run_throughput_benchmark(num_tasks)
    lat_stats = run_latency_benchmark(num_tasks)
    
    print("\nThroughput Summary:")
    print(f"  Enqueue: {tp_stats['enqueue_throughput_tasks_per_sec']:.2f} tasks/sec")
    print(f"  Dequeue: {tp_stats['dequeue_throughput_tasks_per_sec']:.2f} tasks/sec")
    
    print("\nLatency Summary (ms):")
    for action, p in lat_stats.items():
        print(f"  {action.capitalize()}: p50={p['p50']:.2f}ms, p95={p['p95']:.2f}ms, p99={p['p99']:.2f}ms, mean={p['mean']:.2f}ms")
        
    # Write CSV
    csv_path = "gtm_engine/benchmark/benchmark_results.csv"
    with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Adapter", "Metric", "Value"])
        writer.writerow([datetime.datetime.now().isoformat(), adapter_name, "enqueue_throughput", tp_stats["enqueue_throughput_tasks_per_sec"]])
        writer.writerow([datetime.datetime.now().isoformat(), adapter_name, "dequeue_throughput", tp_stats["dequeue_throughput_tasks_per_sec"]])
        for action, p in lat_stats.items():
            for pct, val in p.items():
                writer.writerow([datetime.datetime.now().isoformat(), adapter_name, f"{action}_latency_{pct}", val])
                
    print(f"\nSaved raw CSV results to: {csv_path}")

    # Generate Markdown Report
    report_md = f"""# GTM Engine V4.2 Performance Benchmark Report

Generated on: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Active Queue Adapter: **{adapter_name}**

---

## 🚀 Throughput Profile
Measures tasks completed per second under standard workload pressure.

| Action | Total Tasks | Duration (seconds) | Throughput (tasks/sec) |
| :--- | :--- | :--- | :--- |
| **Enqueue** | {num_tasks} | {tp_stats['enqueue_duration_sec']:.4f} | **{tp_stats['enqueue_throughput_tasks_per_sec']:.2f}** |
| **Dequeue + Ack** | {num_tasks} | {tp_stats['dequeue_duration_sec']:.4f} | **{tp_stats['dequeue_throughput_tasks_per_sec']:.2f}** |

---

## ⏱️ Latency Percentiles
Detailed latency profiles measured in milliseconds.

| Operation | Average (mean) | p50 (Median) | p95 Percentile | p99 (Tail Latency) |
| :--- | :--- | :--- | :--- | :--- |
| **Enqueue** | {lat_stats['enqueue']['mean']:.2f} ms | {lat_stats['enqueue']['p50']:.2f} ms | {lat_stats['enqueue']['p95']:.2f} ms | {lat_stats['enqueue']['p99']:.2f} ms |
| **Dequeue** | {lat_stats['dequeue']['mean']:.2f} ms | {lat_stats['dequeue']['p50']:.2f} ms | {lat_stats['dequeue']['p95']:.2f} ms | {lat_stats['dequeue']['p99']:.2f} ms |
| **Acknowledge (Ack)** | {lat_stats['ack']['mean']:.2f} ms | {lat_stats['ack']['p50']:.2f} ms | {lat_stats['ack']['p95']:.2f} ms | {lat_stats['ack']['p99']:.2f} ms |

---

## 📈 Analysis & Recommendations
- **SQLite Latency:** SQLite provides sub-millisecond execution times for local development, making it highly optimal for fast test suites.
- **Production Scalability:** Switch to the `RedisQueueAdapter` in multi-worker environments to prevent SQLite write-locking and scale horizontally.
"""
    
    report_path = "gtm_engine/benchmark/benchmark_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
        
    print(f"Saved Markdown report to: {report_path}")

    # Optional: matplotlib chart generation
    try:
        import matplotlib.pyplot as plt
        categories = ['Enqueue Latency (mean)', 'Dequeue Latency (mean)', 'Ack Latency (mean)']
        values = [lat_stats['enqueue']['mean'], lat_stats['dequeue']['mean'], lat_stats['ack']['mean']]
        
        plt.figure(figsize=(8, 5))
        plt.bar(categories, values, color=['#3498db', '#2ecc71', '#e74c3c'])
        plt.ylabel('Latency (ms)')
        plt.title(f'GTM Queue Latency Profile ({adapter_name})')
        plt.grid(axis='y', linestyle='--', alpha=0.7)
        
        chart_path = "gtm_engine/benchmark/latency_chart.png"
        plt.savefig(chart_path)
        plt.close()
        print(f"Saved latency visualization chart to: {chart_path}")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
