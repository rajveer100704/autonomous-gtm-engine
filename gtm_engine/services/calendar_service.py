"""
services/calendar_service.py — Unified Calendar Booking Service.

Supports:
  - Calendly
  - Cal.com
  - Google Calendar Booking
  - Microsoft Bookings

Provides a unified interface `calendar_service.book(...)` to record booked meetings,
automatically update CRM statuses, and notify the event bus.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from gtm_engine.services.crm_service import CRMService
from gtm_engine.events.event_bus import bus as event_bus, EventType
from gtm_engine.audit.audit_log import audit_log

log = logging.getLogger("gtm.calendar_service")

PLATFORM_PATTERNS = {
    "calendly": re.compile(r"calendly\.com/[a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)*", re.IGNORECASE),
    "cal_com": re.compile(r"cal\.com/[a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)*", re.IGNORECASE),
    "google_calendar": re.compile(r"calendar\.google\.com/calendar/[a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)*", re.IGNORECASE),
    "microsoft_bookings": re.compile(r"outlook\.office365\.com/owa/calendar/[a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)*", re.IGNORECASE),
}


class CalendarService:
    """Unified manager for scheduling operations and platform detection."""

    def __init__(self) -> None:
        self.crm = CRMService()

    def detect_booking_link(self, text: str) -> dict[str, str] | None:
        """
        Scan text for calendar booking URLs.
        Returns {"platform": "calendly|cal_com|google_calendar|microsoft_bookings", "url": "..."}
        or None if no links found.
        """
        for platform, pattern in PLATFORM_PATTERNS.items():
            match = pattern.search(text)
            if match:
                url = match.group(0)
                # Ensure protocol is present
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                return {"platform": platform, "url": url}
        return None

    def book(
        self,
        lead_id: int,
        platform: str,
        booking_url: str,
        slot_time: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """
        Unified method to register/execute a booking for a lead.
        Updates lead status in CRM and fires MEETING_BOOKED event.
        """
        with audit_log.span(
            "calendar_service", "book",
            reason=f"Booking via {platform}",
            lead_id=lead_id,
            inputs={"platform": platform, "url": booking_url, "slot_time": slot_time},
        ) as span:
            log.info("calendar_service: booking lead %d via %s (%s)", lead_id, platform, booking_url)

            # Update CRM status
            from gtm_engine.crm import db
            db.record_event(lead_id, "meeting_booked", metadata=f"Platform: {platform}. Link: {booking_url}. Time: {slot_time or 'TBD'}")

            # Also ensure lead status moves to 'engaged' / 'meeting_booked'
            self.crm.update_lead_status(lead_id, "meeting_booked")

            # Fire meeting booked event
            event_bus.emit(
                EventType.MEETING_BOOKED,
                lead_id=lead_id,
                platform=platform,
                booking_url=booking_url,
                slot_time=slot_time,
                notes=notes,
            )

            result = {
                "success": True,
                "lead_id": lead_id,
                "platform": platform,
                "booking_url": booking_url,
                "slot_time": slot_time or "pending",
                "status": "booked",
            }
            span.set_outputs(result)
            return result


# Singleton
calendar_service = CalendarService()
