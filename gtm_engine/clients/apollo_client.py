"""
Apollo.io client — sources + enriches leads matching the ICP.
Real endpoint: POST https://api.apollo.io/v1/mixed_people/search
Docs: https://apolloio.github.io/apollo-api-docs/

In mock_mode (no APOLLO_API_KEY, or GTM_MOCK_MODE=true) this returns
realistic synthetic leads so the rest of the pipeline can be built/tested
without burning API credits.
"""
import requests
from gtm_engine.config import settings

APOLLO_SEARCH_URL = "https://api.apollo.io/v1/mixed_people/search"

_MOCK_LEADS = [
    {
        "full_name": "Priya Nair",
        "title": "Head of Sales",
        "company": "Bramble Legal Tech",
        "domain": "bramblelegal.com",
        "email": "priya.nair@bramblelegal.com",
        "linkedin_url": "https://linkedin.com/in/priyanair-mock",
        "raw": {"employee_count": 42, "industry": "Legal Tech"},
    },
    {
        "full_name": "Daniel Ortiz",
        "title": "Founder & CEO",
        "company": "Northwind Docs",
        "domain": "northwinddocs.io",
        "email": "daniel@northwinddocs.io",
        "linkedin_url": "https://linkedin.com/in/danielortiz-mock",
        "raw": {"employee_count": 18, "industry": "SaaS"},
    },
    {
        "full_name": "Grace Lim",
        "title": "VP Sales",
        "company": "Ferra Contracts",
        "domain": "ferracontracts.com",
        "email": "grace.lim@ferracontracts.com",
        "linkedin_url": "https://linkedin.com/in/gracelim-mock",
        "raw": {"employee_count": 65, "industry": "Document Management"},
    },
]


def _generate_mock_leads(count: int) -> list[dict]:
    if count <= len(_MOCK_LEADS):
        return _MOCK_LEADS[:count]

    leads = []
    for i in range(count):
        base = dict(_MOCK_LEADS[i % len(_MOCK_LEADS)])
        if i >= len(_MOCK_LEADS):
            suffix = i // len(_MOCK_LEADS) + 1
            base = dict(base)
            base["full_name"] = f"{base['full_name']} {suffix}"
            base["company"] = f"{base['company']} {suffix}"
            base["domain"] = f"lead{i}.{base['domain']}"
            base["email"] = f"lead{i}.{base['email']}"
        leads.append(base)
    return leads


def search_leads(titles=None, industries=None, per_page: int = 10) -> list[dict]:
    titles = titles or list(settings.icp_titles)
    industries = industries or list(settings.icp_industries)

    if settings.mock_mode or not settings.apollo_api_key:
        return _generate_mock_leads(per_page)

    headers = {"Content-Type": "application/json", "Cache-Control": "no-cache"}
    payload = {
        "api_key": settings.apollo_api_key,
        "person_titles": titles,
        "organization_industry_tag_ids": industries,
        "organization_num_employees_ranges": [
            f"{settings.icp_company_size_min},{settings.icp_company_size_max}"
        ],
        "page": 1,
        "per_page": per_page,
    }
    resp = requests.post(APOLLO_SEARCH_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    leads = []
    for person in data.get("people", []):
        org = person.get("organization", {}) or {}
        leads.append({
            "full_name": person.get("name"),
            "title": person.get("title"),
            "company": org.get("name"),
            "domain": org.get("primary_domain") or org.get("website_url"),
            "email": person.get("email"),
            "linkedin_url": person.get("linkedin_url"),
            "raw": person,
        })
    return leads
