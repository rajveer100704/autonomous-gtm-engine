"""
Lightweight tracing. This is deliberately NOT OpenTelemetry — no collector,
no Jaeger, no Prometheus to stand up. It answers the same question a
reviewer actually asks first ("how long does each agent take, and does it
ever fail?") with one SQL table. Swapping this for real OTel spans later
is a contained change: wrap the same call sites with an OTel tracer
instead of `span()`, same call sites, same shape.
"""
import time
import contextlib
import datetime

from gtm_engine.crm import db


@contextlib.contextmanager
def span(trace_id: str, agent: str, lead_id: int = None):
    start = time.monotonic()
    error = None
    try:
        yield
    except Exception as e:
        error = str(e)
        raise
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            db.record_span(trace_id, agent, duration_ms, success=(error is None),
                            error=error, lead_id=lead_id)
        except Exception:
            pass  # tracing must never break the pipeline


def new_trace_id(lead_ref) -> str:
    return f"trace-{lead_ref}-{int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)}"
