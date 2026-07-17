"""
Web search client used to GROUND the research agent in real, current
information instead of letting the LLM guess from company name/size alone.

Uses Tavily (https://tavily.com) — a single API key, search-engine-for-LLMs
product that's the simplest of the three the review suggested (Tavily /
Exa / Firecrawl). Swapping providers means rewriting only this file —
research_agent.py just calls `search_company()`.
"""
import requests
from gtm_engine.config import settings

TAVILY_URL = "https://api.tavily.com/search"


def search(query: str, max_results: int = 5) -> list[dict]:
    """
    Generic web search — for company intelligence sub-agents.
    Returns list of {title, url, content} dicts.
    """
    if settings.mock_mode or not settings.tavily_api_key:
        # Return generic mock results for any query
        domain = "example.com"
        company = query.split('"')[1] if '"' in query else query[:20]
        return _mock_search(company, domain)

    resp = requests.post(
        TAVILY_URL,
        json={
            "api_key": settings.tavily_api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", "")[:500],
        }
        for r in data.get("results", [])
    ]


def search_company(company: str, domain: str) -> list[dict]:

    """
    Returns a list of {title, url, snippet} for recent, relevant results
    about this company — funding, hiring, product launches, news.
    """
    if settings.mock_mode or not settings.tavily_api_key:
        return _mock_search(company, domain)

    query = f"{company} ({domain}) recent news funding hiring product launch"
    resp = requests.post(
        TAVILY_URL,
        json={
            "api_key": settings.tavily_api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:400],
        }
        for r in data.get("results", [])
    ]


def _mock_search(company: str, domain: str) -> list[dict]:
    """Realistic canned search results so the pipeline is testable offline."""
    return [
        {
            "title": f"{company} raises seed round to expand sales team",
            "url": f"https://{domain}/blog/seed-round",
            "snippet": f"{company} announced a seed round and plans to grow "
                       f"its go-to-market team, adding 3 new sales roles this quarter.",
        },
        {
            "title": f"{company} careers — open roles",
            "url": f"https://{domain}/careers",
            "snippet": f"{company} is hiring for Account Executive and Sales "
                       f"Development Representative roles as it scales outbound.",
        },
        {
            "title": f"{company} on LinkedIn",
            "url": f"https://linkedin.com/company/{domain.split('.')[0]}",
            "snippet": f"Recent post from {company} leadership discussing "
                       f"the pain of manual contract review slowing down deal cycles.",
        },
    ]
