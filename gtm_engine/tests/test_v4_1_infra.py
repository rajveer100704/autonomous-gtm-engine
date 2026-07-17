import os
import pytest
from fastapi.testclient import TestClient

# Ensure test DB profile is isolated
os.environ["GTM_PROFILE"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///test_gtm_v4_1.db"

import pytest
from gtm_engine.crm import db as db_module
from gtm_engine.utils import feature_flags
from gtm_engine.observability import observability
from gtm_engine.events import bus, EventType
from gtm_engine.api import app


@pytest.fixture(autouse=True)
def clean_db():
    db_module.recreate_db_engine("sqlite:///test_gtm_v4_1.db")
    db_module.engine.dispose()
    for db_file in ["test_gtm_v4_1.db", "test_graph_state_v4_1.db"]:
        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except Exception:
                pass
    db_module.init_db()
    
    # Register/ensure DLQ, queue, etc.
    from gtm_engine.task_queue.execution_queue import execution_queue
    from gtm_engine.task_queue.dead_letter_queue import init_dlq
    execution_queue._ensure_table()
    init_dlq()
    
    with db_module.engine.begin() as conn:
        for table in reversed(db_module.metadata.sorted_tables):
            try:
                conn.execute(table.delete())
            except Exception:
                pass
    yield
    db_module.engine.dispose()
    for db_file in ["test_gtm_v4_1.db", "test_graph_state_v4_1.db"]:
        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except Exception:
                pass


# ── 1. Feature Flags & Profile Tests ──────────────────────────────────────

def test_feature_flags_and_profile():
    assert feature_flags.get_profile() == "test"
    # Defaults in test environment
    assert feature_flags.is_feature_enabled("USE_OTEL") is False
    assert feature_flags.is_feature_enabled("USE_REDIS") is False


# ── 2. Observability Service Tests ────────────────────────────────────────

def test_observability_service_fallback():
    # Verify tracing works with fallback mock span
    with observability.emit_span("test_span", attributes={"key": "val"}) as span:
        span.set_attribute("extra", 42)
        span.record_exception(ValueError("Ouch"))
    
    # Should run with no errors even when OTel libraries are not in active use
    observability.emit_metric("prospects_sourced", 5.0, labels={"agent": "planner"})
    observability.log(20, "Test log message")


# ── 3. Domain Event Bus Decoupling Tests ──────────────────────────────────

def test_domain_events_update_crm_and_memory():
    # Insert a lead and an outreach draft
    lead_id = db_module.insert_lead({
        "full_name": "Jane Doe",
        "title": "VP Sales",
        "company": "Acme Corp",
        "domain": "acme.com",
        "email": "jane@acme.com",
    })
    outreach_id = db_module.save_outreach(
        lead_id=lead_id, channel="email", step=0,
        subject="Hello Jane", body="We love Acme Corp.",
        scheduled_for=None, status="drafted"
    )

    # Initial check: outreach status is drafted
    out_row = db_module.all_outreach()[0]
    assert out_row["status"] == "drafted"

    # Emit the EMAIL_SENT event via the Event Bus
    bus.emit(EventType.EMAIL_SENT, lead_id=lead_id, outreach_id=outreach_id, subject="Hello Jane")

    # CRM should be updated asynchronously/via event subscriber
    out_row_after = db_module.all_outreach()[0]
    assert out_row_after["status"] == "sent"
    assert out_row_after["sent_at"] is not None

    # Memory should be updated via event subscriber
    from gtm_engine.memory.lead_memory import lead_memory
    mem = lead_memory.load(lead_id)
    assert "Hello Jane" in mem.get("past_emails", [])


# ── 4. API Endpoints & Versioning Tests ───────────────────────────────────

def test_api_versioning_and_health_metrics():
    client = TestClient(app)
    
    # Versioned routes
    r_health = client.get("/api/v1/health")
    assert r_health.status_code == 200
    assert r_health.json() == {"status": "ok"}

    # Root legacy fallback routes
    r_health_legacy = client.get("/health")
    assert r_health_legacy.status_code == 200
    assert r_health_legacy.json() == {"status": "ok"}

    # Live endpoint
    r_live = client.get("/api/v1/live")
    assert r_live.status_code == 200
    assert r_live.json() == {"status": "live"}

    # Ready endpoint
    r_ready = client.get("/api/v1/ready")
    assert r_ready.status_code == 200
    assert r_ready.json()["status"] == "ready"

    # Metrics endpoint
    r_metrics = client.get("/metrics")
    assert r_metrics.status_code == 200
    assert "gtm_tasks_processed_total" in r_metrics.text
