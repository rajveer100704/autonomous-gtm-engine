"""
Website Agent — analyzes a company's homepage, pricing, and about page.
Extracts: product description, target customers, value proposition, positioning.
"""
from __future__ import annotations
import logging
log = logging.getLogger("gtm.website_agent")


def analyze_website(domain: str, company_name: str, lead_id: int | None = None) -> dict:
    """
    Search for company website content and extract key signals.
    Uses Tavily to fetch page content without requiring direct crawling.
    """
    from gtm_engine.config import settings
    from gtm_engine.clients.search_client import search

    if settings.mock_mode:
        return {
            "summary": f"{company_name} offers SaaS solutions for enterprise teams.",
            "signals": [f"{company_name} has a self-serve pricing page"],
            "sources": [],
        }

    try:
        query = f"site:{domain} OR \"{company_name}\" product features pricing"
        results = search(query, max_results=3)
        if not results:
            return {"summary": "", "signals": [], "sources": []}

        snippets = " ".join(r.get("content", "") for r in results[:3])
        sources = [{"title": r.get("title", ""), "url": r.get("url", "")} for r in results[:3]]

        from gtm_engine.clients.llm_client import complete
        prompt = f"""From the following web content about {company_name}, extract:
1. A one-sentence product summary
2. Up to 3 website signals (product focus, pricing model, target customer)

Web content: {snippets[:1500]}

Return JSON: {{"summary": "...", "signals": ["...", "..."]}}"""

        import json, re
        raw = complete(prompt, agent_name="website_agent", lead_id=lead_id, temperature=0.1)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            parsed["sources"] = sources
            return parsed
    except Exception as exc:
        log.warning("WebsiteAgent: %s", exc)

    return {"summary": "", "signals": [], "sources": []}
