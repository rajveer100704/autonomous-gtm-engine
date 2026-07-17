"""
Tests for GTM Engine V3 Architecture.
All tests run with GTM_MOCK_MODE=true (no real API calls).
"""
import os
import asyncio
import datetime
import pytest
import requests

os.environ["GTM_MOCK_MODE"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///test_gtm_v3.db"

from gtm_engine.crm import db


@pytest.fixture(autouse=True)
def clean_db():
    from gtm_engine.crm import db as db_module
    from gtm_engine.utils.circuit_breaker import _registry
    from gtm_engine.task_queue.execution_queue import execution_queue
    from gtm_engine.task_queue.dead_letter_queue import init_dlq
    from gtm_engine.audit.audit_log import audit_log

    db_module.recreate_db_engine("sqlite:///test_gtm_v3.db")
    db_module.engine.dispose()
    # Reset circuit breakers
    _registry.clear()
    for db_file in ["test_gtm_v3.db", "test_graph_state_v3.db"]:

        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except Exception:
                pass
    db_module.init_db()
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
    _registry.clear()
    for db_file in ["test_gtm_v3.db", "test_graph_state_v3.db"]:
        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except Exception:
                pass






# ═══════════════════════════════════════════════════════════════════════════
# Priority 1: Parallel Company Intelligence
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_gather_company_intelligence_async():
    from gtm_engine.agents.company_intelligence import gather_company_intelligence_async
    
    result = await gather_company_intelligence_async(
        domain="stripe.com",
        company_name="Stripe",
        lead_id=1,
    )
    assert "website" in result
    assert "news" in result
    assert "hiring" in result
    assert "tech_stack" in result
    assert result["confidence"] in ("low", "medium", "high")
    assert "Stripe" in result["merged_summary"]


# ═══════════════════════════════════════════════════════════════════════════
# Priority 2: Two-stage Evaluation Agent
# ═══════════════════════════════════════════════════════════════════════════

def test_evaluation_agent_hard_checks():
    from gtm_engine.agents import evaluation_agent
    
    lead = {
        "first_name": "Sarah",
        "company": "Stripe",
        "title": "Head of Sales",
    }
    
    # 1. Healthy email should pass hard checks (must be >= 20 words and contain recipients name & company)
    good_email = {
        "subject": "Quick question for Sarah",
        "body": (
            "Hi Sarah, I saw that Stripe is expanding its outbound sales team. "
            "I'd love to connect to discuss how we can help simplify your contract "
            "and document workflow systems so your reps spend more time selling."
        )
    }
    res = evaluation_agent._hard_checks(good_email, lead)
    assert res["passed"] is True, f"Hard checks failed: {res.get('failures')}"
    
    # 2. Email with unresolved placeholder should fail
    bad_email = {
        "subject": "Quick question",
        "body": "Hi {first_name}, I saw that Stripe is expanding..."
    }
    res2 = evaluation_agent._hard_checks(bad_email, lead)
    assert res2["passed"] is False
    assert any("unresolved_placeholder" in f for f in res2["failures"])


def test_evaluation_agent_spam_checking():
    from gtm_engine.agents import evaluation_agent
    
    lead = {"first_name": "Bob", "company": "Stripe"}
    spammy_email = {
        "subject": "FREE offer for Bob",
        "body": "Act now! Risk free congratulations Stripe. Double your cash bonus click here now!"
    }
    res = evaluation_agent._hard_checks(spammy_email, lead)
    assert res["passed"] is False
    assert any("high_spam_score" in f for f in res["failures"])


def test_evaluation_agent_full_evaluate():
    from gtm_engine.agents import evaluation_agent
    
    lead = {
        "first_name": "Sarah",
        "company": "Stripe",
        "title": "VP Sales",
    }
    email = {
        "subject": "Connecting with Sarah @ Stripe",
        "body": (
            "Hi Sarah, noticed Stripe is hiring sales leads. Let's schedule "
            "a 15-minute quick call next week to talk about document workflow automation "
            "and see if we can help speed up your contracts."
        )
    }
    
    res = evaluation_agent.evaluate(email, lead, pain_points=["outbound hiring spikes"])
    assert res["approved"] is True
    assert res["combined_score"] > 5.0
    assert "llm_review" in res


# ═══════════════════════════════════════════════════════════════════════════
# Priority 3: Calendar Service & Calendar Agent
# ═══════════════════════════════════════════════════════════════════════════

def test_calendar_service_link_detection():
    from gtm_engine.services.calendar_service import calendar_service
    
    # Test Calendly
    t1 = "Sure, book here: calendly.com/user-name/15min or email me."
    d1 = calendar_service.detect_booking_link(t1)
    assert d1 is not None
    assert d1["platform"] == "calendly"
    assert "https://calendly.com" in d1["url"]
    
    # Test Cal.com
    t2 = "Use cal.com/acme/demo to schedule."
    d2 = calendar_service.detect_booking_link(t2)
    assert d2 is not None
    assert d2["platform"] == "cal_com"
    
    # Test no link
    assert calendar_service.detect_booking_link("Let's chat tomorrow at 2pm.") is None


def test_calendar_agent_auto_book():
    from gtm_engine.agents.calendar_agent import handle_meeting_intent
    db.init_db()
    
    lead_id = db.insert_lead({
        "full_name": "Alice Johnson",
        "first_name": "Alice",
        "company": "Calm",
        "email": "alice@calm.com",
    })
    
    reply = "Happy to talk. Book on my cal here: calendly.com/alice-calm/intro"
    result = handle_meeting_intent(lead_id, reply)
    
    assert result["action"] == "auto_registered"
    assert result["platform"] == "calendly"
    assert result["url"] == "https://calendly.com/alice-calm/intro"
    
    # Verify CRM updated
    lead = db.get_lead(lead_id)
    assert lead["status"] == "meeting_booked"


def test_calendar_agent_draft_invite_reply():
    from gtm_engine.agents.calendar_agent import handle_meeting_intent
    from gtm_engine.task_queue.execution_queue import execution_queue
    db.init_db()
    
    lead_id = db.insert_lead({
        "full_name": "Alice Johnson",
        "first_name": "Alice",
        "company": "Calm",
        "email": "alice@calm.com",
    })
    
    reply = "Let's book something for next week!"
    result = handle_meeting_intent(lead_id, reply)
    
    assert result["action"] == "draft_sent_to_queue"
    assert result["draft_reply"] is not None
    assert "superdocs/demo" in result["draft_reply"]["body"]
    
    # Verify queue has task
    assert execution_queue.pending_count() == 1


# ═══════════════════════════════════════════════════════════════════════════
# Priority 4: Campaign Analytics
# ═══════════════════════════════════════════════════════════════════════════

def test_campaign_analytics_aggregations():
    db.init_db()
    
    # Create leads
    l1 = db.insert_lead({"full_name": "L1", "first_name": "L1", "company": "C1", "title": "CEO", "raw": {"industry": "SaaS"}})
    l2 = db.insert_lead({"full_name": "L2", "first_name": "L2", "company": "C2", "title": "VP Sales", "raw": {"industry": "Legal Tech"}})
    
    # Save outreach
    db.save_outreach(l1, "email", 0, "Subj A", "Body A", datetime.datetime.utcnow(), "sent", "A")
    db.save_outreach(l2, "email", 0, "Subj B", "Body B", datetime.datetime.utcnow(), "sent", "B")
    
    # Record events
    db.record_event(l1, "lead_sourced")
    db.record_event(l2, "lead_sourced")
    db.record_event(l1, "email_sent")
    db.record_event(l2, "email_sent")
    db.record_event(l1, "email_opened")
    db.record_event(l1, "email_replied")
    db.record_event(l1, "meeting_booked")
    
    # Insert a reply classification
    with db.engine.begin() as conn:
        conn.execute(db.reply_classifications.insert().values(
            lead_id=l1, classification="meeting_request", confidence="high",
            raw_reply="Let's book", created_at=datetime.datetime.utcnow()
        ))
        
    analytics = db.campaign_analytics()
    
    assert analytics["funnel"]["sourced"] == 2
    assert analytics["funnel"]["sent"] == 2
    assert analytics["funnel"]["opened"] == 1
    assert analytics["funnel"]["replies"] == 1
    assert analytics["funnel"]["meetings"] == 1
    
    assert analytics["rates"]["open_rate"] == 50.0
    assert analytics["rates"]["reply_rate"] == 50.0
    
    assert "SaaS" in analytics["industries"]
    assert analytics["industries"]["SaaS"]["sourced"] == 1
    assert "Founder/CEO" in analytics["personas"]


# ═══════════════════════════════════════════════════════════════════════════
# Priority 5: Idempotency
# ═══════════════════════════════════════════════════════════════════════════

def test_execution_queue_idempotency():
    from gtm_engine.task_queue.execution_queue import execution_queue
    db.init_db()
    
    task1 = {"type": "email", "to": "test@stripe.com", "subject": "Hi", "body": "B"}
    task2 = {"type": "email", "to": "test@stripe.com", "subject": "Hi", "body": "B-changed"}
    
    ikey = execution_queue.make_key(lead_id=42, channel="email", step=0, template_version="A")
    
    id1 = execution_queue.enqueue(lead_id=42, task=task1, idempotency_key=ikey)
    id2 = execution_queue.enqueue(lead_id=42, task=task2, idempotency_key=ikey)
    
    assert id1 == id2
    assert execution_queue.pending_count() == 1
    
    # Dequeue task, check it is the first one (unmodified payload)
    dequeued = execution_queue.dequeue()
    assert dequeued["body"] == "B"


# ═══════════════════════════════════════════════════════════════════════════
# Priority 6: Audit Logs
# ═══════════════════════════════════════════════════════════════════════════

def test_audit_logger_spans():
    from gtm_engine.audit.audit_log import audit_log
    
    # 1. Success span
    with audit_log.span("test_agent", "action_x", lead_id=99) as span:
        # Perform action
        result = {"msg": "done"}
        span.set_outputs(result)
        
    recent = audit_log.recent(limit=1)
    assert len(recent) == 1
    assert recent[0]["agent"] == "test_agent"
    assert recent[0]["action"] == "action_x"
    assert recent[0]["success"] is True
    assert recent[0]["outputs"] == {"msg": "done"}
    assert recent[0]["duration_ms"] >= 0
    
    # 2. Failure span
    with pytest.raises(ValueError):
        with audit_log.span("test_agent", "action_fail", lead_id=99) as span:
            raise ValueError("API error")
            
    recent_fail = audit_log.recent(limit=2)
    assert recent_fail[0]["action"] == "action_fail"
    assert recent_fail[0]["success"] is False
    assert "API error" in recent_fail[0]["error"]


# ═══════════════════════════════════════════════════════════════════════════
# Priority 7: Retry Policies
# ═══════════════════════════════════════════════════════════════════════════

def test_retry_decorator_retryable():
    from gtm_engine.utils.retry import retry
    
    attempts = 0
    
    @retry(max_attempts=3, base_backoff=0.01)
    def flaky_api():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            # Raise a retryable requests ConnectionError
            raise requests.exceptions.ConnectionError("Network timeout")
        return "success"
        
    res = flaky_api()
    assert res == "success"
    assert attempts == 3


def test_retry_decorator_non_retryable():
    from gtm_engine.utils.retry import retry
    
    attempts = 0
    
    # Simulates a 400 Bad Request
    response = requests.Response()
    response.status_code = 400
    http_error = requests.exceptions.HTTPError(response=response)
    
    @retry(max_attempts=3, base_backoff=0.01)
    def failing_api():
        nonlocal attempts
        attempts += 1
        raise http_error
        
    with pytest.raises(requests.exceptions.HTTPError):
        failing_api()
        
    # Should fail immediately without retrying
    assert attempts == 1


# ═══════════════════════════════════════════════════════════════════════════
# Priority 8: Dead Letter Queue (DLQ)
# ═══════════════════════════════════════════════════════════════════════════

def test_dead_letter_queue_resolution():
    from gtm_engine.task_queue.dead_letter_queue import dead_letter_queue
    
    task = {"type": "email", "to": "bad@lead.com", "body": "..."}
    dlq_id = dead_letter_queue.add(
        lead_id=7,
        task=task,
        failure_reason="Permanent bounce 550",
        retry_count=3,
        idempotency_key="key-123",
    )
    
    # 1. Retrieve entry
    pending = dead_letter_queue.pending()
    assert len(pending) >= 1
    assert pending[0]["id"] == dlq_id
    assert pending[0]["status"] == "pending_review"
    
    # 2. Resolve entry
    dead_letter_queue.resolve(dlq_id, "Marked as invalid email address by admin")
    entry = dead_letter_queue.get(dlq_id)
    assert entry["status"] == "resolved"


# ═══════════════════════════════════════════════════════════════════════════
# Priority 9: Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════════

def test_circuit_breaker_flow():
    from gtm_engine.utils.circuit_breaker import get_circuit, CircuitState, CircuitOpenError
    
    cb = get_circuit("test_service", failure_threshold=2, recovery_timeout=0.2)
    assert cb.state == CircuitState.CLOSED
    
    # 1. First failure
    with pytest.raises(ValueError):
        with cb:
            raise ValueError("Failure 1")
    assert cb.state == CircuitState.CLOSED
    
    # 2. Second failure -> trips circuit to OPEN
    with pytest.raises(ValueError):
        with cb:
            raise ValueError("Failure 2")
    assert cb.state == CircuitState.OPEN
    
    # 3. Request blocked while OPEN
    with pytest.raises(CircuitOpenError):
        with cb:
            # Should never reach here
            pass
            
    # 4. Wait for recovery timeout
    import time
    time.sleep(0.25)
    
    # First request after timeout probes in HALF_OPEN
    assert cb.state == CircuitState.HALF_OPEN
    
    # Succeeded probe -> resets to CLOSED
    with cb:
        pass
    assert cb.state == CircuitState.CLOSED
