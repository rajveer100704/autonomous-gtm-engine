import logging
from typing import Any, Dict, Optional, Generator
from contextlib import contextmanager

from gtm_engine.utils.feature_flags import is_feature_enabled

log = logging.getLogger("gtm.observability")

class ObservabilityService:
    """
    Unified observability wrapper for metrics, traces, and structured logging.
    Gracefully degrades to local fallback logging if OTel is disabled/unavailable.
    """
    def __init__(self):
        self._otel_enabled = is_feature_enabled("USE_OTEL")
        self._tracer = None
        self._meter = None
        self._counters = {}
        self._histograms = {}

        if self._otel_enabled:
            self._init_otel()

    def _init_otel(self) -> None:
        try:
            from opentelemetry import trace, metrics
            self._tracer = trace.get_tracer("gtm_engine")
            self._meter = metrics.get_meter("gtm_engine")
            log.info("ObservabilityService: OpenTelemetry tracing and metrics initialized.")
        except ImportError:
            log.warning("ObservabilityService: OpenTelemetry libraries not installed. Falling back to local logging.")
            self._otel_enabled = False

    @contextmanager
    def emit_span(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> Generator[Any, None, None]:
        """
        Emits an execution span. Uses OpenTelemetry if enabled, otherwise logs duration.
        """
        import time
        start_time = time.perf_counter()
        attrs = attributes or {}

        if self._otel_enabled and self._tracer:
            with self._tracer.start_as_current_span(name, attributes=attrs) as span:
                yield span
        else:
            # Fallback local timing span
            log.info("Observability [SPAN START]: '%s' | attributes=%s", name, attrs)
            try:
                # We yield a mock span object that supports set_attribute interface
                class MockSpan:
                    def set_attribute(self, key, value):
                        attrs[key] = value
                    def record_exception(self, exc):
                        log.error("Observability [SPAN ERROR]: %s", exc)
                yield MockSpan()
            finally:
                duration = time.perf_counter() - start_time
                log.info("Observability [SPAN END]: '%s' | duration=%.3f seconds", name, duration)

    def emit_metric(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """
        Record a metric measurement. Uses Prometheus/OTel if enabled, otherwise logs.
        """
        lbls = labels or {}
        if self._otel_enabled and self._meter:
            try:
                # Dynamically track meters
                if name not in self._counters:
                    self._counters[name] = self._meter.create_counter(
                        name, description=f"Counter for {name}"
                    )
                self._counters[name].add(value, lbls)
            except Exception as e:
                log.error("ObservabilityService: failed to emit OTel metric: %s", e)
        else:
            log.info("Observability [METRIC]: '%s' value=%.2f | labels=%s", name, value, lbls)

    def log(self, level: int, message: str, *args, **kwargs) -> None:
        """Structured logging with contextual dictionary support."""
        log.log(level, message, *args, **kwargs)

# Global observability singleton
observability = ObservabilityService()
