"""
Cold email generator. Personalizes subject + body using the lead's
enrichment data, research summary, and detected pain points.
"""
import json
from gtm_engine.clients import llm_client
from gtm_engine.config import settings

SYSTEM_PROMPT = """[AGENT:EMAIL] You are an expert cold email copywriter for B2B SaaS.
Write a short (under 90 words), specific, non-generic first-touch cold
email. No fluff, no "I hope this finds you well", no more than one
exclamation mark. Reference the specific pain point given. End with a
low-friction call to action (a question, not "let's schedule a call").
You will be given a Style: value — honor it:
  - "direct": lead with the specific pain point and value prop up front.
  - "curiosity": open with an observation or question that earns a reply
    before mentioning the product at all.
Respond ONLY as JSON: {"subject": "...", "body": "..."}
Use {first_name}, {company}, {pitch}, {sender_name} as literal placeholders
in the body — do not resolve them yourself."""


def generate(lead: dict, pain_points: list[str], lead_id: int = None, style: str = "direct",
             feedback: str = None) -> dict:
    context = (
        f"Lead: {lead.get('first_name')} , title: {lead.get('title')}, "
        f"company: {lead.get('company')}\n"
        f"Top pain point: {pain_points[0] if pain_points else 'general inefficiency'}\n"
        f"Our pitch: {settings.sender_product_pitch}\n"
        f"Style: {style}"
    )
    if feedback:
        context += f"\nThis is a REWRITE. Prior draft was rejected. Reviewer feedback: {feedback}"
    raw = llm_client.complete(SYSTEM_PROMPT, context, agent="email", lead_id=lead_id)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"subject": "quick question", "body": raw.strip()}

    fmt_kwargs = dict(
        first_name=lead.get("first_name", "there"),
        company=lead.get("company", "your company"),
        pitch=settings.sender_product_pitch,
        sender_name=settings.sender_name,
    )
    try:
        subject = parsed.get("subject", "").format(**fmt_kwargs)
    except KeyError:
        subject = parsed.get("subject", "")
    try:
        body = parsed.get("body", "").format(**fmt_kwargs)
    except KeyError:
        body = parsed.get("body", "")
    return {"subject": subject, "body": body}


VARIANT_STYLES = {"A": "direct", "B": "curiosity"}


def generate_variants(lead: dict, pain_points: list[str], lead_id: int = None) -> dict:
    """Returns {'A': {...}, 'B': {...}} — two stylistically distinct email drafts."""
    return {
        label: generate(lead, pain_points, lead_id=lead_id, style=style)
        for label, style in VARIANT_STYLES.items()
    }
