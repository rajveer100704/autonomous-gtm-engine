import logging
from prometheus_client import Counter, Histogram, Gauge

log = logging.getLogger("gtm.observability.metrics")

# Prometheus Metrics Definitions
TASK_PROCESSED_COUNTER = Counter(
    "gtm_tasks_processed_total",
    "Total number of GTM outreach tasks processed by workers",
    ["type", "status"] # e.g. type='email'/'linkedin', status='success'/'failed'
)

NODE_LATENCY_HISTOGRAM = Histogram(
    "gtm_graph_node_duration_seconds",
    "Latency of campaign graph node executions",
    ["node_name"]
)

QUEUE_DEPTH_GAUGE = Gauge(
    "gtm_queue_depth_current",
    "Current number of pending tasks in the execution queue"
)

CIRCUIT_BREAKER_STATE_GAUGE = Gauge(
    "gtm_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half-open)",
    ["service"]
)

DLQ_COUNT_GAUGE = Gauge(
    "gtm_dlq_count_current",
    "Current count of unresolved tasks in the Dead Letter Queue"
)
