"""
Tech Stack Agent — infers the technology stack from public signals.

Used for:
- Product-led messaging ("Since you're using Stripe, you know payments infra matters...")
- Competitive intelligence ("You're using Salesforce for CRM...")
- Integration angle ("We integrate natively with HubSpot")
"""
from __future__ import annotations
import logging
log = logging.getLogger("gtm.tech_stack_agent")

# Common tech stack signals to look for
TECH_KEYWORDS = [
    "Salesforce", "HubSpot", "Stripe", "Twilio", "AWS", "Azure", "GCP",
    "React", "Next.js", "Python", "PostgreSQL", "MongoDB", "Redis",
    "Zendesk", "Intercom", "Slack", "Notion", "Jira", "GitHub",
    "Mixpanel", "Segment", "Amplitude", "Datadog", "Snowflake",
]


def detect_tech_stack(domain: str, lead_id: int | None = None) -> dict:
    """Infer tech stack from public signals (job postings, GitHub, blog, etc.)."""
    from gtm_engine.config import settings
    from gtm_engine.clients.search_client import search

    if settings.mock_mode:
        return {
            "summary": "Uses modern SaaS stack including AWS and PostgreSQL.",
            "signals": ["Uses AWS infrastructure", "Likely uses Stripe for payments"],
            "detected_tools": ["AWS", "PostgreSQL"],
            "sources": [],
        }

    try:
        query = f'"{domain}" OR "{domain.split(".")[0]}" (Salesforce OR HubSpot OR AWS OR Stripe OR PostgreSQL OR "tech stack")'
        results = search(query, max_results=3)
        if not results:
            return {"summary": "", "signals": [], "detected_tools": [], "sources": []}

        all_content = " ".join(r.get("content", "") for r in results[:3])
        sources = [{"title": r.get("title", ""), "url": r.get("url", "")} for r in results[:3]]

        # Quick keyword scan (no LLM needed)
        detected = [kw for kw in TECH_KEYWORDS if kw.lower() in all_content.lower()]

        if not detected:
            return {"summary": "", "signals": [], "detected_tools": [], "sources": sources}

        signals = [f"Uses {tool}" for tool in detected[:4]]
        summary = f"Tech stack includes: {', '.join(detected[:5])}"

        return {
            "summary": summary,
            "signals": signals,
            "detected_tools": detected,
            "sources": sources,
        }
    except Exception as exc:
        log.warning("TechStackAgent: %s", exc)
        return {"summary": "", "signals": [], "detected_tools": [], "sources": []}
