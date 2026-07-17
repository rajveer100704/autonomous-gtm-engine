"""
News Agent — finds recent press, funding announcements, partnerships, product launches.
Recent news is the highest-value personalization signal ("Congrats on your Series A...").
"""
from __future__ import annotations
import logging
log = logging.getLogger("gtm.news_agent")


def find_news(company_name: str, lead_id: int | None = None) -> dict:
    """Search for recent news about the company (last 90 days)."""
    from gtm_engine.config import settings
    from gtm_engine.clients.search_client import search

    if settings.mock_mode:
        return {
            "summary": f"{company_name} recently expanded their enterprise offering.",
            "signals": [f"{company_name} announced new product features last month"],
            "sources": [],
        }

    try:
        query = f'"{company_name}" (funding OR launch OR partnership OR expansion OR "Series A" OR "Series B") 2024 OR 2025'
        results = search(query, max_results=3)
        if not results:
            return {"summary": "", "signals": [], "sources": []}

        snippets = " ".join(r.get("content", "")[:400] for r in results[:3])
        sources = [{"title": r.get("title", ""), "url": r.get("url", "")} for r in results[:3]]

        from gtm_engine.clients.llm_client import complete
        import json, re
        prompt = f"""From these news snippets about {company_name}, extract:
1. A one-sentence summary of the most relevant recent news
2. Up to 2 specific signals useful for cold outreach personalization

Snippets: {snippets[:1200]}

Return JSON: {{"summary": "...", "signals": ["...", "..."]}}"""

        raw = complete(prompt, agent_name="news_agent", lead_id=lead_id, temperature=0.1)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            parsed["sources"] = sources
            return parsed
    except Exception as exc:
        log.warning("NewsAgent: %s", exc)

    return {"summary": "", "signals": [], "sources": []}
