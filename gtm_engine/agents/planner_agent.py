"""
Planner Agent — top-level campaign planner.

Receives the campaign request and outputs a structured execution plan:
{
    "icp": {"titles": [...], "industries": [...], "company_size": "..."},
    "max_leads": 10,
    "channels": ["email", "linkedin"],
    "objective": "Book Demo",
    "notes": "Focus on companies that recently raised Series A"
}

The planner sets the intent; Strategy Agent decides per-lead tactics.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("gtm.planner_agent")


def plan_campaign(
    objective: str = "Book Demo",
    max_leads: int = 10,
    focus: str = "",
    lead_id: int | None = None,
) -> dict[str, Any]:
    """
    Generate a campaign execution plan.

    Args:
        objective: What success looks like (e.g., "Book Demo", "Warm Introduction")
        max_leads: How many leads to target
        focus: Optional free-text focus (e.g., "Series A companies in LegalTech")

    Returns:
        Campaign plan dict consumed by the LangGraph planner_node
    """
    from gtm_engine.config import settings
    from gtm_engine.clients.llm_client import complete

    base_plan = {
        "objective": objective,
        "max_leads": max_leads,
        "icp": {
            "titles": list(settings.icp_titles),
            "industries": list(settings.icp_industries),
            "company_size_min": settings.icp_company_size_min,
            "company_size_max": settings.icp_company_size_max,
        },
        "channels": ["email", "linkedin"],
        "focus": focus or "Standard ICP outreach",
    }

    if settings.mock_mode or not focus:
        log.info("PlannerAgent: returning base plan (objective=%s, max_leads=%d)", objective, max_leads)
        return base_plan

    # Enrich plan with LLM reasoning when a focus is provided
    prompt = f"""You are a GTM campaign planner.
Campaign objective: {objective}
Max leads: {max_leads}
Focus: {focus}
Current ICP: titles={settings.icp_titles}, industries={settings.icp_industries}

Suggest any adjustments to the ICP targeting, messaging emphasis, or channel priority
given the focus. Be concise. Return a short JSON with keys: icp_notes, channel_priority, messaging_angle.
"""
    try:
        import json, re
        raw = complete(prompt, agent_name="planner", lead_id=lead_id, temperature=0.3)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            enrichment = json.loads(match.group())
            base_plan.update(enrichment)
    except Exception as exc:
        log.warning("PlannerAgent: enrichment failed, using base plan: %s", exc)

    log.info("PlannerAgent: campaign planned (objective=%s)", objective)
    return base_plan
