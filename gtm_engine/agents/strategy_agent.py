"""
Strategy Agent — decides the optimal outreach sequence for each lead.

Returns a rich structured plan:
{
    "objective": "Book Demo",
    "confidence": 0.91,
    "recommended_sequence": [
        {"channel": "linkedin", "delay_days": 0, "reason": "Active daily poster"},
        {"channel": "email",    "delay_days": 2, "reason": "Primary channel for CTOs"},
        {"channel": "followup", "delay_days": 6, "reason": "If no reply after email"}
    ],
    "stop_conditions": ["reply", "meeting_booked"],
    "skip": false,
    "skip_reason": null
}

The LangGraph workflow reads this and routes execution accordingly.
"""
from __future__ import annotations

import logging

log = logging.getLogger("gtm.strategy_agent")


def decide_outreach_strategy(
    lead: dict,
    research: dict,
    pain_points: list[str],
    memory: dict | None = None,
    lead_id: int | None = None,
) -> dict:
    """
    Use Gemini to decide the optimal outreach strategy for this lead.

    Args:
        lead: Enriched lead dict
        research: Research output (summary, signals, confidence)
        pain_points: Detected business challenges
        memory: Optional lead memory (past interactions, objections)
        lead_id: For LLM cost tracking

    Returns:
        Strategy dict with objective, confidence, recommended_sequence, stop_conditions
    """
    from gtm_engine.config import settings
    from gtm_engine.clients.llm_client import complete

    if settings.mock_mode:
        return _mock_strategy(lead, pain_points, memory)

    memory_context = ""
    if memory and (memory.get("objections") or memory.get("last_reply_classification")):
        objections = "; ".join(memory.get("objections", []))
        last_reply = memory.get("last_reply_classification", "")
        memory_context = f"""
MEMORY (previous interactions with this lead):
- Known objections: {objections or 'none'}
- Last reply classification: {last_reply or 'none'}
- Notes: {memory.get('notes', '')}
Adjust the strategy based on this context.
"""

    has_linkedin = bool(lead.get("linkedin_url"))
    company_size = lead.get("company_size_bucket", "")
    confidence = research.get("confidence", "low")

    prompt = f"""You are a GTM strategy agent for B2B outreach.

LEAD:
- Name: {lead.get('full_name', '')}
- Title: {lead.get('title', '')}
- Company: {lead.get('company', '')}
- LinkedIn: {'available' if has_linkedin else 'not available'}
- Company size: {company_size}

RESEARCH CONFIDENCE: {confidence}
TOP PAIN POINTS: {'; '.join(pain_points[:3]) if pain_points else 'unknown'}

{memory_context}

Decide the optimal outreach strategy. Return ONLY valid JSON matching this exact schema:
{{
    "objective": "Book Demo|Warm Introduction|Educational Outreach",
    "confidence": 0.0-1.0,
    "recommended_sequence": [
        {{"channel": "email|linkedin|followup", "delay_days": 0, "reason": "one line"}}
    ],
    "stop_conditions": ["reply", "meeting_booked"],
    "skip": false,
    "skip_reason": null
}}

Rules:
- If no LinkedIn URL, do not include linkedin in sequence
- If research confidence is low, start with a softer educational email
- If memory shows objections, acknowledge them in the strategy objective
- Maximum 3 steps in recommended_sequence
- If the lead is clearly not ICP, set skip=true"""

    try:
        import json
        raw = complete(prompt, agent_name="strategy", lead_id=lead_id, temperature=0.2)
        # Extract JSON from response
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            strategy = json.loads(match.group())
            _validate_strategy(strategy, has_linkedin)
            log.info(
                "StrategyAgent: %s → %s (conf=%.2f)",
                lead.get("company"), strategy.get("objective"), strategy.get("confidence", 0)
            )
            return strategy
    except Exception as exc:
        log.warning("StrategyAgent: LLM failed, using default: %s", exc)

    return _default_strategy(has_linkedin)


def _validate_strategy(strategy: dict, has_linkedin: bool) -> None:
    """Sanitize strategy in-place to remove invalid channels."""
    seq = strategy.get("recommended_sequence", [])
    strategy["recommended_sequence"] = [
        step for step in seq
        if not (step.get("channel") == "linkedin" and not has_linkedin)
    ]
    if "confidence" not in strategy:
        strategy["confidence"] = 0.7
    if "stop_conditions" not in strategy:
        strategy["stop_conditions"] = ["reply", "meeting_booked"]


def _default_strategy(has_linkedin: bool) -> dict:
    seq = [{"channel": "email", "delay_days": 0, "reason": "Primary B2B channel"}]
    if has_linkedin:
        seq.append({"channel": "linkedin", "delay_days": 2, "reason": "Reinforce email outreach"})
    seq.append({"channel": "followup", "delay_days": 6, "reason": "If no reply"})
    return {
        "objective": "Book Demo",
        "confidence": 0.7,
        "recommended_sequence": seq,
        "stop_conditions": ["reply", "meeting_booked"],
        "skip": False,
        "skip_reason": None,
    }


def _mock_strategy(lead: dict, pain_points: list, memory: dict | None) -> dict:
    has_linkedin = bool(lead.get("linkedin_url"))
    strategy = _default_strategy(has_linkedin)

    # If memory shows they already rejected us, adjust
    if memory and memory.get("last_reply_classification") in ("not_interested", "unsubscribe"):
        strategy["skip"] = True
        strategy["skip_reason"] = "Previously unsubscribed or marked not interested"
        strategy["confidence"] = 0.95

    return strategy
