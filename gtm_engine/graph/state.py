"""
LangGraph Campaign State — the shared state object passed between all graph nodes.

Design principles (from langgraph skill):
- TypedDict with explicit reducers (append vs. overwrite)
- Partial updates only — nodes return only what they changed
- State is the single source of truth for the entire workflow
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict
from operator import add


def _merge_dicts(a: dict, b: dict) -> dict:
    """Reducer: merge two dicts, b values override a."""
    return {**a, **b}


def _last(a: Any, b: Any) -> Any:
    """Reducer: keep the latest value (simple overwrite)."""
    return b


class LeadState(TypedDict, total=False):
    """Per-lead state within the campaign graph."""
    lead_id: int
    enriched: dict
    research: dict
    company_intelligence: dict
    pain_points: list[str]
    email_copy: dict
    linkedin_copy: dict
    strategy: dict
    execution_results: list[dict]
    memory_context: str
    errors: list[str]
    completed: bool


class CampaignState(TypedDict, total=False):
    """
    Top-level campaign state.

    Reducers:
      - leads, results: append (list accumulator)
      - errors: append
      - plan, current_lead: overwrite (last value wins)
    """
    # Campaign metadata
    plan: Annotated[dict, _last]
    objective: Annotated[str, _last]
    max_leads: Annotated[int, _last]

    # Lead pipeline
    raw_leads: Annotated[list, _last]          # Apollo output
    current_lead_index: Annotated[int, _last]   # pointer
    current_lead: Annotated[dict, _last]        # enriched lead being processed

    # Per-lead accumulated results
    results: Annotated[list[dict], add]        # appended by each lead
    errors: Annotated[list[str], add]          # appended on failure

    # Current lead's intermediate state (overwritten each iteration)
    research: Annotated[dict, _last]
    company_intelligence: Annotated[dict, _last]
    pain_points: Annotated[list[str], _last]
    email_copy: Annotated[dict, _last]
    linkedin_copy: Annotated[dict, _last]
    strategy: Annotated[dict, _last]
    memory_context: Annotated[str, _last]

    # Execution tracking
    queue_ids: Annotated[list[int], add]
    execution_results: Annotated[list[dict], add]

    # Control
    campaign_complete: Annotated[bool, _last]
