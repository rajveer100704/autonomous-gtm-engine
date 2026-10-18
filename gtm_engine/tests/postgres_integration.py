"""Real PostgreSQL integration tests. TEST_DATABASE_URL is mandatory by design."""
import concurrent.futures
import datetime as dt
import os

import pytest
from sqlalchemy import text

from gtm_engine.crm import db


@pytest.fixture(autouse=True)
def disposable_postgres():
    url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.fail("TEST_DATABASE_URL must point to a disposable PostgreSQL database; refusing to skip")
    db.recreate_db_engine(url)
    with db.engine.connect() as conn:
        assert conn.dialect.name == "postgresql"
        conn.execute(text("SELECT 1"))
    # CI/local runbook points this suite at a disposable empty database only.
    db.metadata.drop_all(db.engine)
    db.init_db()
    yield
    db.metadata.drop_all(db.engine)
    db.engine.dispose()


def create_exposure():
    sent_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    lead_id = db.insert_lead({"full_name": "PG Test", "title": "CTO", "company": "PG", "domain": "pg.test", "email": "pg-test@example.test", "raw": {}})
    outreach_id = db.save_outreach(lead_id, "email", 0, "Hello", "Body", sent_at, "drafted", "A")
    db.update_outreach_status(outreach_id, "sent", sent_at=sent_at)
    decision_id = db.record_decision(
        lead_id=lead_id, action="A", action_value="direct", assignment_probability=0.5,
        policy_name="randomized_baseline_v1", decision_key=f"pg-decision-{outreach_id}",
        outreach_id=outreach_id, channel="email", sequence_step=0, decision_type="email_style",
    )
    return lead_id, outreach_id, decision_id, sent_at


def test_existing_records_survive_additive_schema_reinitialization():
    lead_id = db.insert_lead({"full_name": "Keep Me", "title": "CTO", "company": "Keep", "domain": "keep.test", "email": "keep@example.test", "raw": {}})
    db.provider_event_receipts.drop(db.engine)
    db.init_db()
    assert db.get_lead(lead_id)["full_name"] == "Keep Me"
    assert db.list_provider_receipts() == []


def test_postgres_json_and_timezone_tolerant_outcome_roundtrip():
    lead_id, outreach_id, decision_id, sent_at = create_exposure()
    result = db.record_learning_outcome(
        lead_id=lead_id, outreach_id=outreach_id, decision_id=decision_id,
        event_type="email_opened", event_key="pg-open-1",
        occurred_at=sent_at + dt.timedelta(days=1), source="sendgrid_webhook",
        metadata={"provider": "sendgrid", "nested": {"count": 1}},
    )
    assert result["status"] == "recorded"
    stored = db.all_learning_outcomes()[0]
    assert stored["metadata_json"]["nested"]["count"] == 1
    assert stored["event_key"] == "pg-open-1"


def test_concurrent_duplicate_outcome_ingestion_is_idempotent():
    lead_id, outreach_id, decision_id, sent_at = create_exposure()
    args = dict(
        lead_id=lead_id, outreach_id=outreach_id, decision_id=decision_id,
        event_type="email_replied", event_key="pg-concurrent-reply",
        occurred_at=sent_at + dt.timedelta(days=1), source="sendgrid_webhook",
    )
    def ingest(_):
        return db.record_learning_outcome(**args)["status"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(ingest, range(16)))
    assert statuses.count("recorded") == 1
    assert statuses.count("duplicate") == 15
    assert len(db.all_learning_outcomes()) == 1


def test_provider_receipt_unique_key_survives_concurrent_retries():
    import hashlib
    digest = hashlib.sha256(b"signed-event").hexdigest()
    def ingest(_):
        return db.create_or_get_provider_receipt("sendgrid", "event-unique", "open", digest)["receipt"]["id"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(ingest, range(16)))
    assert len(set(ids)) == 1
    assert len(db.list_provider_receipts()) == 1
