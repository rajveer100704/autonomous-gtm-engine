"""
Company research agent. Grounds Gemini in real, current search results
(via Tavily — see clients/search_client.py) instead of letting it infer a
"buying context" from nothing but company name/size, which is what the v1
version did. Results are cached per-domain (crm/db.py research_cache
table) so repeated pipeline runs don't re-search the same company.
"""
import json
from gtm_engine.clients import llm_client, search_client
from gtm_engine.config import settings
from gtm_engine.crm import db

SYSTEM_PROMPT = """[AGENT:RESEARCH] You are a company research analyst supporting a B2B sales team.
You will be given real search results about a company (title/url/snippet
for each) plus its industry and size. Synthesize them into a concise
buying-context summary and a short list of concrete signals that suggest
they might need document/contract automation software. Ground every claim
in the search results provided — do not invent facts that aren't
supported by them. If the results don't support a signal, omit it rather
than guessing. Respond ONLY as JSON:
{"summary": "...", "signals": ["...", "..."]}"""


def gather_context(lead: dict, search_results: list[dict]) -> str:
    results_text = "\n".join(
        f"- {r['title']} ({r['url']}): {r['snippet']}" for r in search_results
    ) or "(no search results found)"
    return (
        f"Company: {lead.get('company')}\n"
        f"Domain: {lead.get('domain')}\n"
        f"Industry: {(lead.get('raw') or {}).get('industry', 'unknown')}\n"
        f"Size bucket: {lead.get('company_size_bucket', 'unknown')}\n\n"
        f"Search results:\n{results_text}"
    )


def research(lead: dict, lead_id: int = None) -> dict:
    domain = lead.get("domain", "")

    cached = db.get_cached_research(domain, settings.research_cache_ttl_hours)
    if cached:
        return {
            "summary": cached["summary"],
            "signals": cached["signals"].split("; ") if cached["signals"] else [],
            "sources": [dict(zip(("title", "url"), s.split("|", 1)))
                        for s in cached["sources"].split("; ") if s] if cached["sources"] else [],
            "confidence": cached["confidence"] or "low",
            "from_cache": True,
        }

    search_results = search_client.search_company(lead.get("company", ""), domain)
    context = gather_context(lead, search_results)
    raw = llm_client.complete(SYSTEM_PROMPT, context, agent="research", lead_id=lead_id)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"summary": raw.strip(), "signals": []}

    signals = parsed.get("signals", [])
    # Confidence is grounded in how much of the summary/signals is actually
    # backed by search results, not a made-up number — more results and
    # more signals extracted from them means a higher-confidence read.
    if len(search_results) >= 2 and len(signals) >= 2:
        confidence = "high"
    elif search_results and signals:
        confidence = "medium"
    else:
        confidence = "low"

    sources = [{"title": r["title"], "url": r["url"]} for r in search_results]
    sources_str = "; ".join(f"{s['title']}|{s['url']}" for s in sources)

    db.upsert_research_cache(
        domain, parsed.get("summary", ""), "; ".join(signals), sources_str, confidence
    )
    parsed["sources"] = sources
    parsed["confidence"] = confidence
    parsed["from_cache"] = False
    return parsed
