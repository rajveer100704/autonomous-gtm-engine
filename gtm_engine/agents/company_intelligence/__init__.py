"""
Company Intelligence — splits research into focused sub-agents.

Each sub-agent is responsible for one signal type:
  - WebsiteAgent:  company homepage, pricing, about
  - NewsAgent:     recent press, funding, partnerships
  - HiringAgent:   job postings → growth/pain signals
  - TechStackAgent: BuiltWith-style tech inference

The orchestrator (research_agent.py) merges all four.
This module provides the merge function used by the LangGraph node.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("gtm.company_intelligence")


def gather_company_intelligence(
    domain: str,
    company_name: str,
    lead_id: int | None = None,
) -> dict[str, Any]:
    """
    Run all four intelligence sub-agents and merge into one rich profile (synchronous).
    """
    results: dict[str, Any] = {}

    from gtm_engine.agents.company_intelligence.website_agent import analyze_website
    from gtm_engine.agents.company_intelligence.news_agent import find_news
    from gtm_engine.agents.company_intelligence.hiring_agent import analyze_hiring
    from gtm_engine.agents.company_intelligence.tech_stack_agent import detect_tech_stack

    results["website"] = analyze_website(domain, company_name, lead_id=lead_id)
    results["news"] = find_news(company_name, lead_id=lead_id)
    results["hiring"] = analyze_hiring(company_name, lead_id=lead_id)
    results["tech_stack"] = detect_tech_stack(domain, lead_id=lead_id)

    return _merge_intelligence_results(results, company_name)


async def gather_company_intelligence_async(
    domain: str,
    company_name: str,
    lead_id: int | None = None,
) -> dict[str, Any]:
    """
    Run all four intelligence sub-agents concurrently using asyncio.gather
    and merge into one rich profile.
    """
    import asyncio
    from gtm_engine.agents.company_intelligence.website_agent import analyze_website
    from gtm_engine.agents.company_intelligence.news_agent import find_news
    from gtm_engine.agents.company_intelligence.hiring_agent import analyze_hiring
    from gtm_engine.agents.company_intelligence.tech_stack_agent import detect_tech_stack

    # Concurrently execute synchronous agents in separate threads to avoid blocking the event loop
    website_task = asyncio.to_thread(analyze_website, domain, company_name, lead_id=lead_id)
    news_task = asyncio.to_thread(find_news, company_name, lead_id=lead_id)
    hiring_task = asyncio.to_thread(analyze_hiring, company_name, lead_id=lead_id)
    tech_stack_task = asyncio.to_thread(detect_tech_stack, domain, lead_id=lead_id)

    web_res, news_res, hiring_res, tech_res = await asyncio.gather(
        website_task, news_task, hiring_task, tech_stack_task
    )

    results = {
        "website": web_res,
        "news": news_res,
        "hiring": hiring_res,
        "tech_stack": tech_res,
    }
    return _merge_intelligence_results(results, company_name)


def _merge_intelligence_results(results: dict[str, Any], company_name: str) -> dict[str, Any]:
    # Merge signals
    signals: list[str] = []
    signals.extend(results["website"].get("signals", []))
    signals.extend(results["news"].get("signals", []))
    signals.extend(results["hiring"].get("signals", []))
    signals.extend(results["tech_stack"].get("signals", []))

    # Determine confidence from signal count
    signal_count = len([s for s in signals if s])
    confidence = "high" if signal_count >= 5 else ("medium" if signal_count >= 2 else "low")

    # Build merged summary
    parts = []
    if results["website"].get("summary"):
        parts.append(results["website"]["summary"])
    if results["news"].get("summary"):
        parts.append(f"Recent: {results['news']['summary']}")
    if results["hiring"].get("summary"):
        parts.append(f"Hiring: {results['hiring']['summary']}")
    if results["tech_stack"].get("summary"):
        parts.append(f"Tech: {results['tech_stack']['summary']}")

    merged_summary = " | ".join(parts) if parts else f"{company_name} — no additional intelligence gathered."

    results["merged_summary"] = merged_summary
    results["merged_signals"] = [s for s in signals if s][:10]
    results["confidence"] = confidence

    log.info(
        "CompanyIntelligence: %s — %d signals, confidence=%s (parallelized)",
        company_name, len(results["merged_signals"]), confidence
    )
    return results

