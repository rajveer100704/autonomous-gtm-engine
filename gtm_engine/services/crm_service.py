"""
CRM Service — the single write path for all agents.

Agents NEVER import from gtm_engine.crm.db directly.
They import from here:

    from gtm_engine.services.crm_service import crm

This layer:
- Validates inputs before persisting
- Emits events through the event bus after writes
- Provides a clean API that hides SQLAlchemy details from agents
- Makes it trivial to swap databases later
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

log = logging.getLogger("gtm.crm_service")


class CRMService:
    """
    Thin service layer over crm.db. All agents write through this class.
    Lazy-imports db to avoid circular imports at module load time.
    """

    def _db(self):
        from gtm_engine.crm import db
        return db

    # ── Leads ─────────────────────────────────────────────────────────────
    def insert_lead(self, enriched: dict) -> int:
        lead_id = self._db().insert_lead(enriched)
        log.info("CRM: inserted lead %s (%s)", lead_id, enriched.get("full_name"))
        self._emit("lead_sourced", lead_id)
        return lead_id

    def update_lead_status(self, lead_id: int, status: str) -> None:
        self._db().update_lead_status(lead_id, status)
        log.debug("CRM: lead %s → %s", lead_id, status)

    def get_lead(self, lead_id: int) -> dict | None:
        detail = self._db().lead_detail(lead_id)
        return detail.get("lead") if detail else None

    def all_leads(self) -> list[dict]:
        return self._db().all_leads()

    # ── Research ──────────────────────────────────────────────────────────
    def save_research(
        self,
        lead_id: int,
        summary: str,
        signals: str,
        sources: str,
        confidence: str,
    ) -> None:
        self._db().save_research(lead_id, summary, signals, sources, confidence)
        self._emit("research_complete", lead_id, confidence=confidence)

    # ── Pain Points (Detected Business Challenges) ────────────────────────
    def save_pain_points(self, lead_id: int, pain_points: str, confidence: str) -> None:
        self._db().save_pain_points(lead_id, pain_points, confidence)

    # ── Outreach ──────────────────────────────────────────────────────────
    def save_outreach(
        self,
        lead_id: int,
        channel: str,
        step: int,
        subject: str | None,
        body: str,
        variant: str | None = None,
        scheduled_for: datetime.datetime | None = None,
        status: str = "drafted",
    ) -> int:
        if scheduled_for is None:
            scheduled_for = datetime.datetime.now(datetime.timezone.utc)
        return self._db().save_outreach(
            lead_id=lead_id, channel=channel, step=step,
            subject=subject, body=body, variant=variant,
            scheduled_for=scheduled_for, status=status,
        )

    def update_outreach_status(
        self,
        outreach_id: int,
        status: str,
        sent_at: datetime.datetime | None = None,
    ) -> None:
        self._db().update_outreach_status(outreach_id, status, sent_at=sent_at)

    def mark_sent(self, lead_id: int, outreach_id: int, channel: str) -> None:
        """Mark outreach as sent and emit the right event."""
        now = datetime.datetime.now(datetime.timezone.utc)
        self.update_outreach_status(outreach_id, "sent", sent_at=now)
        event_type = "email_sent" if channel == "email" else "linkedin_sent"
        self.record_event(lead_id, event_type, metadata=f"outreach_id={outreach_id}")
        self._emit(event_type, lead_id, outreach_id=outreach_id, channel=channel)

    # ── Events ────────────────────────────────────────────────────────────
    def record_event(
        self,
        lead_id: int,
        event_type: str,
        metadata: str = "",
    ) -> None:
        self._db().record_event(lead_id, event_type, metadata)

    # ── Memory ────────────────────────────────────────────────────────────
    def get_lead_memory(self, lead_id: int) -> dict:
        """Return everything we know about this lead for memory-augmented generation."""
        from gtm_engine.memory.lead_memory import lead_memory
        return lead_memory.load(lead_id)

    def update_lead_memory(self, lead_id: int, **updates) -> None:
        from gtm_engine.memory.lead_memory import lead_memory
        lead_memory.update(lead_id, **updates)
        self._emit("memory_updated", lead_id)

    # ── Execution Queue ───────────────────────────────────────────────────
    def queue_execution(self, lead_id: int, task: dict) -> int:
        from gtm_engine.task_queue.execution_queue import execution_queue
        return execution_queue.enqueue(lead_id, task)

    # ── Internal ──────────────────────────────────────────────────────────
    def _emit(self, event_name: str, lead_id: int | None = None, **metadata) -> None:
        try:
            from gtm_engine.events.event_bus import bus, EventType
            et = EventType(event_name)
            bus.emit(et, lead_id=lead_id, **metadata)
        except (ValueError, ImportError):
            pass  # Unknown event type or bus not wired — non-fatal


# Singleton
crm = CRMService()
