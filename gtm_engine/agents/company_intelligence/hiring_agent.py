"""
Hiring Agent — analyzes job postings to infer growth stage and pain points.

Hiring patterns are strong buying-intent signals:
- "Hiring SDRs" → need outreach automation
- "Hiring Head of RevOps" → scaling GTM
- "Hiring DevOps Engineers" → infrastructure investment
"""
from __future__ import annotations
import logging
log = logging.getLogger("gtm.hiring_agent")


def analyze_hiring(company_name: str, lead_id: int | None = None) -> dict:
    """Search for open roles and infer growth signals."""
    from gtm_engine.config import settings
    from gtm_engine.clients.search_client import search

    if settings.mock_mode:
        return {
            "summary": f"{company_name} is hiring sales and engineering roles.",
            "signals": [
                f"{company_name} is actively hiring SDRs — likely scaling outbound",
                "Engineering headcount growing — product investment stage",
            ],
            "sources": [],
        }

    try:
        query = f'"{company_name}" jobs hiring "we are looking" site:linkedin.com OR site:greenhouse.io OR site:lever.co'
        results = search(query, max_results=3)
        if not results:
            return {"summary": "", "signals": [], "sources": []}

        snippets = " ".join(r.get("content", "")[:400] for r in results[:3])
        sources = [{"title": r.get("title", ""), "url": r.get("url", "")} for r in results[:3]]

        from gtm_engine.clients.llm_client import complete
        import json, re
        prompt = f"""Based on these job posting snippets for {company_name}, extract:
1. A one-sentence summary of hiring trends
2. Up to 2 signals relevant for B2B sales personalization (e.g., "scaling GTM", "infrastructure investment")

Job snippets: {snippets[:1200]}

Return JSON: {{"summary": "...", "signals": ["...", "..."]}}"""

        raw = complete(prompt, agent_name="hiring_agent", lead_id=lead_id, temperature=0.1)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            parsed["sources"] = sources
            return parsed
    except Exception as exc:
        log.warning("HiringAgent: %s", exc)

    return {"summary": "", "signals": [], "sources": []}
