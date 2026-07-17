"""
LinkedIn message generator. Shorter and more casual than email — meant for
a connection request note or an InMail opener.
"""
import json
from gtm_engine.clients import llm_client
from gtm_engine.config import settings

SYSTEM_PROMPT = """[AGENT:LINKEDIN] You are an expert at writing short LinkedIn connection
and outreach notes for B2B sales. Under 45 words. Casual, human, specific
to the pain point given — never salesy, never "I'd love to connect".
Respond ONLY as JSON: {"message": "..."}
Use {first_name} and {company} as literal placeholders — do not resolve
them yourself."""


def generate(lead: dict, pain_points: list[str], lead_id: int = None) -> dict:
    context = (
        f"Lead: {lead.get('first_name')}, title: {lead.get('title')}, "
        f"company: {lead.get('company')}\n"
        f"Top pain point: {pain_points[0] if pain_points else 'general inefficiency'}"
    )
    raw = llm_client.complete(SYSTEM_PROMPT, context, agent="linkedin", lead_id=lead_id)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"message": raw.strip()}

    try:
        message = parsed.get("message", "").format(
            first_name=lead.get("first_name", "there"),
            company=lead.get("company", "your company"),
        )
    except KeyError:
        message = parsed.get("message", "")
    return {"message": message}
