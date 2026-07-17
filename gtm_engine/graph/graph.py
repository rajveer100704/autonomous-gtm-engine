"""
LangGraph Campaign Graph — builds and compiles the stateful multi-agent workflow.

Graph topology:
  START
    ↓
  planner
    ↓
  apollo
    ↓
  ┌─ enrich ──────────────────────────────────────────┐
  │   ↓                                               │
  │  research → intelligence → pain_point → content   │
  │   ↓                                               │
  │  strategy → queue → memory → advance ─────────────┘
  │                                       ↓ (if more leads)
  └────────────────────────────── should_continue ──→ END

Persistence: SQLite checkpointer stores state per thread_id (campaign run).
Human-in-the-loop: interrupt_before=["queue"] if HUMAN_APPROVAL_MODE=true.
"""
from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger("gtm.graph")


def build_campaign_graph(human_approval: bool = False):
    """
    Build and compile the LangGraph campaign graph.

    Args:
        human_approval: If True, graph pauses before the queue_node
                        so a human can review the strategy and content
                        before tasks are enqueued.

    Returns:
        Compiled LangGraph application (callable with .invoke() or .stream())
    """
    try:
        from langgraph.graph import StateGraph, START, END
    except ImportError:
        raise RuntimeError(
            "LangGraph not installed. Run: pip install langgraph"
        )

    from gtm_engine.graph.state import CampaignState
    from gtm_engine.graph.nodes import (
        planner_node, apollo_node, enrichment_node, research_node,
        intelligence_node, pain_point_node, content_node, strategy_node,
        queue_node, memory_update_node, advance_node, should_continue,
    )
    from gtm_engine.agents.goal_manager import goal_manager_node

    # ── Build graph ───────────────────────────────────────────────────────
    graph = StateGraph(CampaignState)

    # Register nodes
    graph.add_node("goal_manager", goal_manager_node)
    graph.add_node("planner", planner_node)
    graph.add_node("apollo", apollo_node)
    graph.add_node("enrich", enrichment_node)
    graph.add_node("research", research_node)
    graph.add_node("intelligence", intelligence_node)
    graph.add_node("pain_point", pain_point_node)
    graph.add_node("content", content_node)
    graph.add_node("strategy", strategy_node)
    graph.add_node("queue", queue_node)
    graph.add_node("memory", memory_update_node)
    graph.add_node("advance", advance_node)

    # ── Linear spine ──────────────────────────────────────────────────────
    graph.add_edge(START, "goal_manager")
    graph.add_edge("goal_manager", "planner")
    graph.add_edge("planner", "apollo")
    graph.add_edge("apollo", "enrich")
    graph.add_edge("enrich", "research")
    graph.add_edge("research", "intelligence")
    graph.add_edge("intelligence", "pain_point")
    graph.add_edge("pain_point", "content")
    graph.add_edge("content", "strategy")
    graph.add_edge("strategy", "queue")
    graph.add_edge("queue", "memory")
    graph.add_edge("memory", "advance")

    # ── Loop or END ───────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "advance",
        should_continue,
        {"enrich": "enrich", "END": END},
    )

    # ── Persistence ───────────────────────────────────────────────────────
    checkpointer = _get_checkpointer()

    # ── Human-in-the-loop ─────────────────────────────────────────────────
    interrupt_before = ["queue"] if human_approval else []

    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )
    log.info(
        "Campaign graph compiled (human_approval=%s, checkpointer=%s)",
        human_approval, type(checkpointer).__name__,
    )
    return compiled


def _get_checkpointer():
    """Return a SQLite checkpointer. Falls back to in-memory if langgraph-checkpoint not installed."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        return SqliteSaver.from_conn_string("gtm_graph_state.db")
    except ImportError:
        try:
            from langgraph.checkpoint.memory import MemorySaver
            return MemorySaver()
        except ImportError:
            return None


def run_campaign_graph(
    max_leads: int = 10,
    objective: str = "Book Demo",
    focus: str = "",
    thread_id: str | None = None,
    human_approval: bool = False,
) -> dict:
    """
    Convenience function: build graph, run it, return results.

    Args:
        max_leads: Number of leads to process
        objective: Campaign objective
        focus: Optional focus hint for the planner
        thread_id: Used for checkpointing (resume interrupted campaigns)
        human_approval: Pause before execution for review

    Returns:
        Final state dict with results list
    """
    from gtm_engine.config import settings

    graph = build_campaign_graph(human_approval=human_approval or settings.human_approval_mode)

    initial_state = {
        "max_leads": max_leads,
        "objective": objective,
        "plan": {"focus": focus},
        "campaign_complete": False,
    }

    config: dict = {}
    if thread_id:
        config = {"configurable": {"thread_id": thread_id}}

    log.info("run_campaign_graph: starting (max_leads=%d, objective=%s)", max_leads, objective)
    final_state = graph.invoke(initial_state, config=config or None)
    results = final_state.get("results", [])
    log.info("run_campaign_graph: completed — %d leads processed", len(results))
    return {"results": results, "errors": final_state.get("errors", [])}
