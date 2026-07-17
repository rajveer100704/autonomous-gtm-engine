"""
Follow-up agent. Given a lead that hasn't replied after N days, drafts the
next sequence step (a lighter-touch nudge, not a re-pitch) and schedules it.
Runs on a cron/APScheduler tick — see scheduler.py.
"""
import json
import datetime
from gtm_engine.clients import llm_client
from gtm_engine.config import settings
from gtm_engine.crm import db
from gtm_engine.agents import reply_classifier_agent

SYSTEM_PROMPT = """[AGENT:FOLLOWUP] You are a B2B sales rep writing a brief, low-pressure
follow-up to a cold email that got no reply. Under 40 words. Do not
re-explain the pitch. Reference that you're following up, add one new
angle or piece of value, and make it easy to say "not now".
Respond ONLY as JSON: {"subject": "...", "body": "..."}
Use {first_name} and {company} as literal placeholders."""


def draft_followup(lead: dict, step: int, lead_id: int = None) -> dict:
    context = f"Lead: {lead.get('first_name')} at {lead.get('company')}. This is follow-up #{step}."
    raw = llm_client.complete(SYSTEM_PROMPT, context, agent="followup", lead_id=lead_id)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"subject": "following up", "body": raw.strip()}

    fmt_kwargs = dict(
        first_name=lead.get("first_name", "there"),
        company=lead.get("company", "your company"),
        pitch=settings.sender_product_pitch,
        sender_name=settings.sender_name,
    )
    try:
        subject = parsed.get("subject", "following up").format(**fmt_kwargs)
    except KeyError:
        subject = parsed.get("subject", "following up")
    try:
        body = parsed.get("body", "").format(**fmt_kwargs)
    except KeyError:
        body = parsed.get("body", "")
    return {"subject": subject, "body": body}


def schedule_followups_for_lead(lead_row: dict, lead_enriched: dict):
    """
    Called right after the first-touch email is drafted. Pre-schedules the
    configured number of follow-ups at fixed intervals; a real send-status
    webhook would cancel these if the lead replies before they fire.
    """
    for step in range(1, settings.max_follow_ups + 1):
        scheduled_for = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            days=settings.follow_up_delay_days * step
        )
        draft = draft_followup(lead_enriched, step, lead_id=lead_row["id"])
        db.save_outreach(
            lead_id=lead_row["id"],
            channel="email",
            step=step,
            subject=draft["subject"],
            body=draft["body"],
            scheduled_for=scheduled_for,
            status="scheduled",
        )


def run_due_followups(sender_fn):
    """
    sender_fn(outreach_row: dict) -> bool   (True if sent successfully)
    Call this on a schedule (see scheduler.py). Kept decoupled from the
    actual email transport so you can point it at SendGrid, Gmail API,
    or a dry-run logger.
    """
    due = db.due_followups(datetime.datetime.now(datetime.timezone.utc))
    results = []
    for row in due:
        sent_ok = sender_fn(row)
        status = "sent" if sent_ok else "failed"
        db.update_outreach_status(
            row["id"], status, sent_at=datetime.datetime.now(datetime.timezone.utc) if sent_ok else None
        )
        results.append({"outreach_id": row["id"], "status": status})
    return results


# --- Adaptive follow-ups: react to a classified reply instead of blindly ---
# --- continuing a fixed schedule.                                        ---

# Category -> (what happens to already-scheduled follow-ups, new lead status)
_REPLY_ACTIONS = {
    "interested":       ("cancel",              "hot"),
    "meeting_request":  ("cancel",              "hot"),
    "not_interested":   ("cancel",              "disqualified"),
    "unsubscribe":      ("cancel",              "disqualified"),
    "not_now":          ("reschedule:30",       "nurture"),
    "out_of_office":    ("reschedule:7",        "sequenced"),
    "needs_more_info":  ("keep",                "engaged"),
}


def handle_reply(lead_id: int, reply_text: str) -> dict:
    """
    Call this when a reply comes in (from an ESP webhook via
    POST /events/track, or directly). Classifies the reply and adapts the
    lead's remaining follow-up sequence instead of blindly continuing it:
      - interested / meeting_request -> cancel remaining follow-ups, mark hot
      - not_interested / unsubscribe -> cancel remaining follow-ups, disqualify
      - not_now                      -> push remaining follow-ups out 30 days, mark nurture
      - out_of_office                -> push remaining follow-ups out 7 days
      - needs_more_info              -> leave the schedule as-is, just flag it
    """
    lead = db.get_lead(lead_id)
    result = reply_classifier_agent.classify(reply_text, lead)
    classification = result.get("classification", "needs_more_info")
    confidence = result.get("confidence", "low")

    db.save_reply_classification(lead_id, classification, confidence, reply_text)

    action, new_status = _REPLY_ACTIONS.get(classification, ("keep", "engaged"))
    outcome = {"lead_id": lead_id, "classification": classification, "confidence": confidence,
               "action": action, "new_status": new_status}

    if action == "cancel":
        outcome["outreach_affected"] = db.cancel_scheduled_outreach(lead_id)
    elif action.startswith("reschedule:"):
        delta_days = int(action.split(":")[1])
        outcome["outreach_affected"] = db.reschedule_scheduled_outreach(lead_id, delta_days)
        outcome["pushed_by_days"] = delta_days
    else:
        outcome["outreach_affected"] = 0

    db.update_lead_status(lead_id, new_status)
    return outcome
