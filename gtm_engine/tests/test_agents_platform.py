"""
Tests for new multi-agent platform components.
All tests run with GTM_MOCK_MODE=true — no real API calls.
"""
import os
os.environ["GTM_MOCK_MODE"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///test_gtm_platform.db"

import pytest
from gtm_engine.crm import db


@pytest.fixture(autouse=True)
def clean_db():
    from gtm_engine.crm import db as db_module
    from gtm_engine.task_queue.execution_queue import execution_queue
    from gtm_engine.task_queue.dead_letter_queue import init_dlq
    from gtm_engine.audit.audit_log import audit_log

    db_module.recreate_db_engine("sqlite:///test_gtm_platform.db")
    db_module.engine.dispose()
    for db_file in ["test_gtm_platform.db", "test_graph_state_platform.db"]:

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





# ── Event Bus ─────────────────────────────────────────────────────────────

def test_event_bus_emits_and_receives():
    from gtm_engine.events.event_bus import EventBus, EventType

    bus = EventBus()
    received = []
    bus.on(EventType.EMAIL_SENT, lambda e: received.append(e.event_type))
    bus.emit(EventType.EMAIL_SENT, lead_id=1, outreach_id=42)

    assert len(received) == 1
    assert received[0] == EventType.EMAIL_SENT


def test_event_bus_global_handler_fires_for_all_events():
    from gtm_engine.events.event_bus import EventBus, EventType

    bus = EventBus()
    all_events = []
    bus.on_any(lambda e: all_events.append(e.event_type))

    bus.emit(EventType.EMAIL_SENT, lead_id=1)
    bus.emit(EventType.LINKEDIN_SENT, lead_id=2)
    bus.emit(EventType.MEETING_BOOKED, lead_id=1)

    assert len(all_events) == 3


def test_event_bus_handler_exception_does_not_crash_bus():
    from gtm_engine.events.event_bus import EventBus, EventType

    bus = EventBus()
    bus.on(EventType.EMAIL_SENT, lambda e: (_ for _ in ()).throw(ValueError("boom")))

    # Should not raise
    bus.emit(EventType.EMAIL_SENT, lead_id=1)


# ── CRM Service ───────────────────────────────────────────────────────────

def test_crm_service_insert_lead_returns_id():
    from gtm_engine.services.crm_service import CRMService
    svc = CRMService()
    db.init_db()
    lead = {
        "first_name": "Jane", "last_name": "Doe", "full_name": "Jane Doe",
        "title": "CTO", "company": "Acme Corp", "domain": "acme.com",
        "email": "jane@acme.com", "linkedin_url": "", "company_size_bucket": "small",
        "raw": {},
    }
    lead_id = svc.insert_lead(lead)
    assert isinstance(lead_id, int)
    assert lead_id > 0


def test_crm_service_agents_do_not_import_db_directly():
    """
    Ensure strategy_agent and planner_agent import crm_service, not raw db.
    This enforces the 'no direct SQL from agents' rule.
    """
    import ast, pathlib
    agents_dir = pathlib.Path("gtm_engine/agents")
    # These agents should NOT have 'from gtm_engine.crm import db'
    disallowed_agents = ["strategy_agent.py", "planner_agent.py"]
    for fname in disallowed_agents:
        path = agents_dir / fname
        if path.exists():
            code = path.read_text()
            assert "from gtm_engine.crm import db" not in code, \
                f"{fname} must not import db directly — use crm_service"


# ── Lead Memory ───────────────────────────────────────────────────────────

def test_lead_memory_returns_empty_for_new_lead():
    from gtm_engine.memory.lead_memory import LeadMemory
    db.init_db()
    mem = LeadMemory()
    result = mem.load(9999)
    assert result["past_emails"] == []
    assert result["objections"] == []
    assert result["last_email_subject"] is None


def test_lead_memory_persists_and_appends():
    from gtm_engine.memory.lead_memory import LeadMemory
    db.init_db()
    mem = LeadMemory()

    # Insert a lead first so FK constraint is satisfied (if any)
    from gtm_engine.orchestrator import run_pipeline
    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]

    mem.update(lead_id, past_emails=["Subject A"], objections=["Already use Clay"])
    result = mem.load(lead_id)
    assert "Subject A" in result["past_emails"]
    assert "Already use Clay" in result["objections"]


def test_lead_memory_appends_without_overwrite():
    from gtm_engine.memory.lead_memory import LeadMemory
    from gtm_engine.orchestrator import run_pipeline
    db.init_db()
    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]

    mem = LeadMemory()
    mem.update(lead_id, past_emails=["Subject A"])
    mem.update(lead_id, past_emails=["Subject B"])

    result = mem.load(lead_id)
    assert "Subject A" in result["past_emails"]
    assert "Subject B" in result["past_emails"]


def test_lead_memory_deduplicates_objections():
    from gtm_engine.memory.lead_memory import LeadMemory
    from gtm_engine.orchestrator import run_pipeline
    db.init_db()
    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]

    mem = LeadMemory()
    mem.update(lead_id, objections=["No budget"])
    mem.update(lead_id, objections=["No budget"])  # duplicate

    result = mem.load(lead_id)
    assert result["objections"].count("No budget") == 1


def test_lead_memory_context_prompt_includes_objections():
    from gtm_engine.memory.lead_memory import LeadMemory
    from gtm_engine.orchestrator import run_pipeline
    db.init_db()
    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]

    mem = LeadMemory()
    mem.update(lead_id, objections=["Already using HubSpot"], last_email_subject="Your Q3 doc process")
    prompt = mem.build_context_prompt(lead_id)
    assert "Already using HubSpot" in prompt
    assert "Your Q3 doc process" in prompt


# ── Execution Queue ───────────────────────────────────────────────────────

def test_execution_queue_enqueue_dequeue():
    from gtm_engine.task_queue.execution_queue import ExecutionQueue
    db.init_db()
    q = ExecutionQueue()

    task = {"type": "email", "to": "jane@acme.com", "subject": "Hi", "body": "Hello"}
    qid = q.enqueue(lead_id=1, task=task)
    assert isinstance(qid, int)

    popped = q.dequeue()
    assert popped is not None
    assert popped["type"] == "email"
    assert popped["queue_id"] == qid


def test_execution_queue_fifo_ordering():
    from gtm_engine.task_queue.execution_queue import ExecutionQueue
    db.init_db()
    q = ExecutionQueue()

    q.enqueue(1, {"type": "email", "order": 1}, priority=5)
    q.enqueue(1, {"type": "email", "order": 2}, priority=5)

    first = q.dequeue()
    second = q.dequeue()
    assert first["order"] == 1
    assert second["order"] == 2


def test_execution_queue_priority_ordering():
    from gtm_engine.task_queue.execution_queue import ExecutionQueue
    db.init_db()
    q = ExecutionQueue()

    q.enqueue(1, {"type": "email", "label": "low"}, priority=10)
    q.enqueue(1, {"type": "email", "label": "high"}, priority=1)

    first = q.dequeue()
    assert first["label"] == "high"  # lower priority number = higher priority


def test_execution_queue_complete_changes_status():
    from gtm_engine.task_queue.execution_queue import ExecutionQueue
    db.init_db()
    q = ExecutionQueue()

    qid = q.enqueue(1, {"type": "email"})
    task = q.dequeue()
    q.complete(task["queue_id"], {"success": True})

    stats = q.stats()
    assert stats.get("completed", 0) == 1
    assert stats.get("pending", 0) == 0


def test_execution_queue_fail_marks_failed():
    from gtm_engine.task_queue.execution_queue import ExecutionQueue
    db.init_db()
    q = ExecutionQueue()

    qid = q.enqueue(1, {"type": "linkedin"})
    task = q.dequeue()
    q.fail(task["queue_id"], "Connection timeout")

    stats = q.stats()
    assert stats.get("failed", 0) == 1


def test_execution_queue_pending_count():
    from gtm_engine.task_queue.execution_queue import ExecutionQueue
    db.init_db()
    q = ExecutionQueue()

    assert q.pending_count() == 0
    q.enqueue(1, {"type": "email"})
    q.enqueue(1, {"type": "linkedin"})
    assert q.pending_count() == 2


# ── Strategy Agent ────────────────────────────────────────────────────────

def test_strategy_agent_returns_valid_schema():
    from gtm_engine.agents.strategy_agent import decide_outreach_strategy

    lead = {
        "first_name": "Jane", "company": "Acme", "title": "CTO",
        "linkedin_url": "https://linkedin.com/in/janedoe",
    }
    research = {"summary": "Acme is a legal tech SaaS.", "confidence": "medium"}
    pain_points = ["Manual doc review slows deals"]

    strategy = decide_outreach_strategy(lead, research, pain_points)

    assert "objective" in strategy
    assert "confidence" in strategy
    assert isinstance(strategy["confidence"], float)
    assert "recommended_sequence" in strategy
    assert isinstance(strategy["recommended_sequence"], list)
    assert len(strategy["recommended_sequence"]) > 0
    assert "stop_conditions" in strategy
    assert "skip" in strategy


def test_strategy_agent_excludes_linkedin_when_no_url():
    from gtm_engine.agents.strategy_agent import decide_outreach_strategy

    lead = {"first_name": "Jane", "company": "Acme", "title": "CTO", "linkedin_url": ""}
    strategy = decide_outreach_strategy(lead, {}, [])

    channels = [s["channel"] for s in strategy["recommended_sequence"]]
    assert "linkedin" not in channels


def test_strategy_agent_skips_lead_with_prior_unsubscribe():
    from gtm_engine.agents.strategy_agent import decide_outreach_strategy

    lead = {"first_name": "Jane", "company": "Acme", "title": "CTO"}
    memory = {"last_reply_classification": "unsubscribe", "objections": [], "notes": ""}

    strategy = decide_outreach_strategy(lead, {}, [], memory=memory)
    assert strategy["skip"] is True


# ── Planner Agent ─────────────────────────────────────────────────────────

def test_planner_agent_returns_valid_plan():
    from gtm_engine.agents.planner_agent import plan_campaign

    plan = plan_campaign(objective="Book Demo", max_leads=5)

    assert plan["objective"] == "Book Demo"
    assert plan["max_leads"] == 5
    assert "icp" in plan
    assert "channels" in plan
    assert "email" in plan["channels"]


def test_planner_agent_icp_matches_settings():
    from gtm_engine.agents.planner_agent import plan_campaign
    from gtm_engine.config import settings

    plan = plan_campaign()
    assert list(plan["icp"]["titles"]) == list(settings.icp_titles)


# ── Company Intelligence ──────────────────────────────────────────────────

def test_company_intelligence_returns_merged_result():
    from gtm_engine.agents.company_intelligence import gather_company_intelligence

    result = gather_company_intelligence("acme.com", "Acme Corp")

    assert "website" in result
    assert "news" in result
    assert "hiring" in result
    assert "tech_stack" in result
    assert "merged_summary" in result
    assert "merged_signals" in result
    assert isinstance(result["merged_signals"], list)
    assert result["confidence"] in ("low", "medium", "high")


def test_company_intelligence_confidence_grows_with_signals():
    from gtm_engine.agents.company_intelligence import gather_company_intelligence

    result = gather_company_intelligence("acme.com", "Acme Corp")
    # In mock mode, all 4 sub-agents return 1-2 signals each → medium/high
    assert result["confidence"] in ("medium", "high")


# ── Gmail Executor (mock) ─────────────────────────────────────────────────

def test_gmail_executor_mock_mode_returns_success():
    from gtm_engine.executors.gmail_executor import send_email

    result = send_email(
        to="jane@acme.com",
        subject="Quick question",
        body="Hi Jane...",
    )
    assert result["success"] is True
    assert "message_id" in result
    assert result.get("mock") is True


def test_gmail_executor_mock_list_replies_returns_empty():
    from gtm_engine.executors.gmail_executor import list_replies

    replies = list_replies("mock-thread-abc")
    assert replies == []


# ── LinkedIn Executor (mock) ──────────────────────────────────────────────

def test_linkedin_executor_mock_connection_request():
    import asyncio
    from gtm_engine.executors.linkedin_executor import LinkedInExecutor

    executor = LinkedInExecutor()
    result = asyncio.run(executor.send_connection_request(
        profile_url="https://linkedin.com/in/johndoe",
        message="Hi John, I noticed your company recently...",
    ))
    assert result["success"] is True
    assert result.get("mock") is True


def test_linkedin_executor_mock_extract_profile():
    import asyncio
    from gtm_engine.executors.linkedin_executor import LinkedInExecutor

    executor = LinkedInExecutor()
    info = asyncio.run(executor.extract_profile_info("https://linkedin.com/in/johndoe"))
    assert "name" in info
    assert "title" in info
    assert "company" in info


# ── Worker (mock) ─────────────────────────────────────────────────────────

def test_worker_processes_email_task():
    from gtm_engine.task_queue.execution_queue import ExecutionQueue
    from gtm_engine.task_queue.worker import Worker
    db.init_db()

    q = ExecutionQueue()
    q.enqueue(1, {
        "type": "email",
        "to": "jane@acme.com",
        "subject": "Test subject",
        "body": "Test body",
    })

    worker = Worker()
    results = worker.process_all()
    assert len(results) == 1
    assert results[0]["success"] is True

    stats = q.stats()
    assert stats.get("completed", 0) == 1


def test_worker_queue_is_empty_after_processing():
    from gtm_engine.task_queue.execution_queue import ExecutionQueue
    from gtm_engine.task_queue.worker import Worker
    db.init_db()

    q = ExecutionQueue()
    q.enqueue(1, {"type": "email", "to": "a@b.com", "subject": "s", "body": "b"})
    q.enqueue(1, {"type": "email", "to": "c@d.com", "subject": "s", "body": "b"})

    Worker().process_all()
    assert q.pending_count() == 0


# ── API New Endpoints ──────────────────────────────────────────────────────

def test_api_queue_status_endpoint():
    from gtm_engine.api import app
    from fastapi.testclient import TestClient
    from gtm_engine.orchestrator import run_pipeline

    db.init_db()
    client = TestClient(app)
    resp = client.get("/queue/status")
    assert resp.status_code == 200
    # Returns dict of status → count (may be empty if no tasks)
    assert isinstance(resp.json(), dict)


def test_api_memory_endpoints():
    from gtm_engine.api import app
    from fastapi.testclient import TestClient
    from gtm_engine.orchestrator import run_pipeline

    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]

    client = TestClient(app)

    # GET memory
    resp = client.get(f"/memory/{lead_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "past_emails" in body

    # PATCH memory
    resp2 = client.patch(f"/memory/{lead_id}", json={"objections": ["No budget Q4"]})
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "updated"

    # Verify update persisted
    resp3 = client.get(f"/memory/{lead_id}")
    assert "No budget Q4" in resp3.json()["objections"]


def test_api_run_campaign_linear_mode_still_works():
    """Backward compat: linear mode (no use_graph) must return same schema as before."""
    from gtm_engine.api import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post("/run-campaign", json={"max_leads": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert "leads" in body


# ── Config ────────────────────────────────────────────────────────────────

def test_config_has_linkedin_credentials_fields():
    from gtm_engine.config import settings
    assert hasattr(settings, "linkedin_email")
    assert hasattr(settings, "linkedin_password")
    assert hasattr(settings, "human_approval_mode")


def test_config_human_approval_defaults_to_true():
    """human_approval_mode must default to True for safety."""
    # In test env we haven't set HUMAN_APPROVAL_MODE, so it reads env (defaults true)
    from gtm_engine.config import Settings
    s = Settings()
    # default is "true" → True
    assert s.human_approval_mode is True


def test_config_mock_mode_is_true_in_test_env():
    from gtm_engine.config import settings
    assert settings.mock_mode is True
