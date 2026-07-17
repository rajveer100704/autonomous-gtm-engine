"""
Enrichment agent: takes a raw Apollo lead and normalizes/augments it
(first name split, company size bucket, domain cleanup) before it's
handed to the research agent. This is where you'd bolt on Clearbit,
BuiltWith, or a firmographic provider later.
"""


def enrich(raw_lead: dict) -> dict:
    full_name = raw_lead.get("full_name", "") or ""
    first_name = full_name.split(" ")[0] if full_name else "there"

    employee_count = (raw_lead.get("raw") or {}).get("employee_count")
    if employee_count:
        if employee_count < 20:
            size_bucket = "micro (<20)"
        elif employee_count < 75:
            size_bucket = "small (20-75)"
        elif employee_count < 250:
            size_bucket = "mid-market (75-250)"
        else:
            size_bucket = "enterprise (250+)"
    else:
        size_bucket = "unknown"

    enriched = dict(raw_lead)
    enriched["first_name"] = first_name
    enriched["company_size_bucket"] = size_bucket
    return enriched
