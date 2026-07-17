"""
Reply classifier agent. Takes the raw text of a lead's reply to an
outreach email and classifies it into one of a fixed set of categories,
which drives adaptive follow-up behavior (see followup_agent.handle_reply).
"""
import json
from gtm_engine.clients import llm_client

CATEGORIES = [
    "interested", "meeting_request", "needs_more_info",
    "not_now", "out_of_office", "not_interested", "unsubscribe",
]

SYSTEM_PROMPT = """[AGENT:REPLY_CLASSIFY] You are a reply classification specialist for B2B sales.
Classify the lead's reply into exactly one of these categories:
interested, meeting_request, needs_more_info, not_now, out_of_office,
not_interested, unsubscribe.
- "meeting_request" = they proactively suggest a time/call, not just general interest.
- "not_now" = they're open in principle but the timing is wrong (e.g. "check back next quarter").
- "out_of_office" = an automated OOO auto-reply, not a real response.
- "unsubscribe" = they explicitly ask to be removed/stop contact.
Respond ONLY as JSON: {"classification": "...", "confidence": "low|medium|high"}"""


def classify(reply_text: str, lead: dict = None) -> dict:
    context = f"Reply from {lead.get('first_name', 'the lead') if lead else 'the lead'}:\n\"\"\"\n{reply_text}\n\"\"\""
    raw = llm_client.complete(SYSTEM_PROMPT, context, agent="reply_classify",
                               lead_id=(lead or {}).get("id"))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"classification": "needs_more_info", "confidence": "low"}

    if parsed.get("classification") not in CATEGORIES:
        parsed["classification"] = "needs_more_info"
    return parsed
