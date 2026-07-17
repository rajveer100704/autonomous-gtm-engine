import os
import pytest
import datetime
from gtm_engine.crm import db as db_module
from gtm_engine.queues.queue_provider import queue_provider
from gtm_engine.queues.queue_adapters import SQLiteQueueAdapter
from gtm_engine.events.event_store import event_store
from gtm_engine.mcp.tool_registry import tool_registry, ToolDefinition
from gtm_engine.agents.goal_manager import Goal, BudgetManager, goal_manager_node


@pytest.fixture(autouse=True)
def clean_db():
    queue_provider.reset()
    db_module.recreate_db_engine("sqlite:///test_gtm_v4_2.db")
    db_module.init_db()
    
    # Ensure queue and event store tables exist to avoid DELETE errors
    from gtm_engine.task_queue.execution_queue import execution_queue
    from gtm_engine.events.event_store import init_event_store
    execution_queue._ensure_table()
    init_event_store()

    from sqlalchemy import text
    with db_module.engine.begin() as conn:
        for table in reversed(db_module.metadata.sorted_tables):
            try:
                conn.execute(table.delete())
            except Exception:
                pass
        for tbl in ["execution_queue", "event_store", "dead_letter_queue"]:
            try:
                conn.execute(text(f"DELETE FROM {tbl}"))
            except Exception as e:
                print(f"CLEANUP ERROR for {tbl}: {e}")
    yield
    db_module.engine.dispose()


# ── 1. Queue Adapter & Provider Tests ─────────────────────────────────────

def test_queue_provider_lazy_loading():
    # SQLite fallback by default in test env
    queue = queue_provider.get_queue()
    assert isinstance(queue, SQLiteQueueAdapter)


def test_sqlite_queue_lifecycle():
    queue = queue_provider.get_queue()
    
    # Enqueue
    qid = queue.enqueue(
        lead_id=12,
        task={"type": "email", "to": "test@example.com"},
        priority=3,
        idempotency_key="unique_key_v42"
    )
    assert qid > 0
    
    # Duplicate enqueue returns original ID
    qid_dup = queue.enqueue(
        lead_id=12,
        task={"type": "email", "to": "test@example.com"},
        priority=3,
        idempotency_key="unique_key_v42"
    )
    assert qid == qid_dup
    
    # Peek
    peeked = queue.peek(qid)
    assert peeked["status"] == "pending"
    assert peeked["lead_id"] == 12
    
    # Dequeue
    task = queue.dequeue()
    assert task is not None
    assert task["queue_id"] == qid
    
    # Ack
    queue.ack(qid, {"status": "ok"})
    peeked_after = queue.peek(qid)
    assert peeked_after["status"] == "completed"


# ── 2. Event Store Persistence & Replay Filters ───────────────────────────

def test_event_store_persistence_and_replays():
    # Ensure event store is initialized
    from gtm_engine.events.event_store import init_event_store
    init_event_store()

    # Store a series of events
    corr_id = "trace-12345"
    e1 = event_store.store_event(
        aggregate_id="lead-1", aggregate_type="lead",
        event_type="email_sent", metadata_payload={"subj": "A"},
        producer="worker", correlation_id=corr_id
    )
    e2 = event_store.store_event(
        aggregate_id="lead-1", aggregate_type="lead",
        event_type="email_replied", metadata_payload={"text": "Yes"},
        producer="gmail", correlation_id=corr_id
    )
    
    # Fetch single
    ev = event_store.get_event(e1)
    assert ev is not None
    assert ev["event_type"] == "email_sent"
    assert ev["producer"] == "worker"
    assert ev["correlation_id"] == corr_id
    
    # Replay by correlation
    events = event_store.replay_by_correlation(corr_id)
    assert len(events) == 2
    assert events[0]["event_type"] == "email_sent"
    assert events[1]["event_type"] == "email_replied"
    
    # Replay by type
    sent_events = [x for x in event_store.replay_by_type("email_sent") if x["correlation_id"] == corr_id]
    assert len(sent_events) == 1
    assert sent_events[0]["event_id"] == e1


# ── 3. MCP Tool Registry Tests ────────────────────────────────────────────

def test_mcp_tool_registry():
    # Verify core tools registered
    t_gmail = tool_registry.get_tool("gmail.send")
    assert t_gmail is not None
    assert "communication" in t_gmail.tags
    assert t_gmail.permissions == ["gmail.send"]
    
    # Check listing
    tools = tool_registry.list_tools()
    assert len(tools) >= 2


# ── 4. Goal Manager & Cost Budget Tests ───────────────────────────────────

def test_goal_manager_node_and_budget_routing():
    # Test Goal construction
    g = Goal(
        objective="Schedule demo",
        success_criteria="meeting_booked",
        constraints=["No spam"],
        budget=0.50,
        priority=1
    )
    assert g.objective == "Schedule demo"
    
    # Test BudgetManager cost-aware routing
    bm = BudgetManager(limit=0.10)
    # Under budget limit -> uses best model for planning
    assert bm.select_model("planning") == "gemini-2.5-pro"
    
    # Exhaust budget
    bm.record_spend(0.12)
    assert bm.is_exhausted() is True
    # Over budget limit -> falls back to flash
    assert bm.select_model("planning") == "gemini-2.5-flash"

    # Test graph node execution
    node_out = goal_manager_node({"objective": "Test campaign objective"})
    assert "plan" in node_out
    assert node_out["plan"]["goal"]["objective"] == "Test campaign objective"
