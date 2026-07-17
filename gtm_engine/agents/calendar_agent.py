"""
agents/calendar_agent.py — Coordinates parsing and reply generation for meeting intents.

Workflow:
  - Lead replies with a meeting request (e.g., "Let's book a call")
  - If they sent a booking link (Calendly, Cal.com, etc.), we extract it and record the booking.
  - If they didn't, we draft a personalized message containing the sender's calendar booking URL.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from gtm_engine.services.calendar_service import calendar_service
from gtm_engine.services.crm_service import CRMService
from gtm_engine.config import settings

log = logging.getLogger("gtm.calendar_agent")


def handle_meeting_intent(lead_id: int, reply_text: str) -> dict[str, Any]:
    """
    Handle a lead reply classified as 'meeting_request'.

    1. Scans the reply for an invite link. If found, auto-books/registers.
    2. If no link is found, drafts a booking reply with the sender's calendar link.
    """
    crm = CRMService()
    lead = crm.get_lead(lead_id)
    if not lead:
        return {"error": "Lead not found", "action": "none"}

    # Try to detect a booking link in the lead's message
    detected = calendar_service.detect_booking_link(reply_text)

    if detected:
        # Lead sent a link -> Auto-register it
        platform = detected["platform"]
        url = detected["url"]
        booking_result = calendar_service.book(
            lead_id=lead_id,
            platform=platform,
            booking_url=url,
            notes=f"Auto-extracted from reply: {reply_text[:100]}...",
        )
        return {
            "action": "auto_registered",
            "platform": platform,
            "url": url,
            "booking_result": booking_result,
            "draft_reply": None,
        }

    # Lead did not send a link -> Generate booking message with sender's link
    sender_cal_platform = os.getenv("SENDER_CALENDAR_PLATFORM", "calendly")
    sender_cal_url = os.getenv("SENDER_CALENDAR_URL", "https://calendly.com/superdocs/demo")

    draft_body = (
        f"Hi {lead.get('first_name', 'there')},\n\n"
        f"Great! I'd love to show you how {settings.sender_company} can help.\n"
        f"Feel free to grab a convenient slot on my calendar here:\n"
        f"{sender_cal_url}\n\n"
        f"Talk soon,\n"
        f"{settings.sender_name}"
    )

    # Draft reply queue task
    draft_task = {
        "type": "email",
        "to": lead.get("email", ""),
        "subject": f"Re: {settings.sender_company} demo slot",
        "body": draft_body,
    }

    # Put task in the execution queue with high priority (1)
    from gtm_engine.task_queue.execution_queue import execution_queue
    ikey = execution_queue.make_key(lead_id, "email", step=99, template_version="calendar_invite")
    qid = execution_queue.enqueue(lead_id, draft_task, priority=1, idempotency_key=ikey)

    return {
        "action": "draft_sent_to_queue",
        "queue_id": qid,
        "draft_reply": draft_task,
        "sender_calendar_url": sender_cal_url,
    }
