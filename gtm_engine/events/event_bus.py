"""
Event Bus — all agents emit typed events; all observers listen.

Usage:
    from gtm_engine.events.event_bus import bus, EventType
    bus.emit(EventType.EMAIL_SENT, lead_id=1, metadata={"outreach_id": 5})
    bus.on(EventType.EMAIL_REPLIED, my_handler)
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Callable, Any

log = logging.getLogger("gtm.event_bus")


class EventType(str, Enum):
    # Outreach events
    EMAIL_SENT = "email_sent"
    EMAIL_OPENED = "email_opened"
    EMAIL_REPLIED = "email_replied"
    EMAIL_FAILED = "email_failed"

    LINKEDIN_SENT = "linkedin_sent"
    LINKEDIN_ACCEPTED = "linkedin_accepted"
    LINKEDIN_REPLIED = "linkedin_replied"
    LINKEDIN_FAILED = "linkedin_failed"

    # Funnel events
    LEAD_SOURCED = "lead_sourced"
    MEETING_BOOKED = "meeting_booked"
    DEAL_WON = "deal_won"
    DISQUALIFIED = "disqualified"

    # Pipeline events
    CAMPAIGN_STARTED = "campaign_started"
    CAMPAIGN_COMPLETED = "campaign_completed"
    RESEARCH_COMPLETE = "research_complete"
    QUALITY_REVIEW_PASSED = "quality_review_passed"
    QUALITY_REVIEW_FAILED = "quality_review_failed"

    # Workflow events
    WORKFLOW_WAKEUP = "workflow_wakeup"
    FOLLOWUP_DUE = "followup_due"
    MEMORY_UPDATED = "memory_updated"


class Event:
    def __init__(self, event_type: EventType, lead_id: int | None = None, **metadata):
        self.event_type = event_type
        self.lead_id = lead_id
        self.metadata = metadata

    def __repr__(self) -> str:
        return f"Event({self.event_type}, lead_id={self.lead_id}, {self.metadata})"


Handler = Callable[[Event], None]


class EventBus:
    """
    Lightweight synchronous in-process event bus.

    Agents emit; subscribers react. The bus also persists events to the CRM
    if a db module is wired in (set `bus.db_recorder = crm_service.record_event`).
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Handler]] = {}
        self._global_handlers: list[Handler] = []
        self.db_recorder: Callable | None = None  # injected at startup

    def on(self, event_type: EventType, handler: Handler) -> None:
        """Register a handler for a specific event type."""
        self._handlers.setdefault(event_type, []).append(handler)

    def on_any(self, handler: Handler) -> None:
        """Register a handler for every event."""
        self._global_handlers.append(handler)

    def emit(self, event_type: EventType, lead_id: int | None = None, **metadata) -> None:
        """Emit an event. Calls all matching handlers synchronously."""
        event = Event(event_type, lead_id=lead_id, **metadata)
        log.debug("EventBus: %s", event)

        # Persist to CRM if recorder is wired
        if self.db_recorder and lead_id is not None:
            try:
                self.db_recorder(lead_id, event_type.value, str(metadata))
            except Exception as exc:
                log.warning("EventBus: db_recorder failed: %s", exc)

        # Persist to EventStore for Event Sourcing/replayability
        try:
            from gtm_engine.events.event_store import event_store
            # Avoid mutating original metadata dict
            meta_copy = dict(metadata)
            aggregate_id = str(lead_id) if lead_id is not None else "global"
            aggregate_type = "lead" if lead_id is not None else "system"
            
            corr_id = meta_copy.pop("correlation_id", None)
            caus_id = meta_copy.pop("causation_id", None)
            prod = meta_copy.pop("producer", "unknown")
            ten_id = meta_copy.pop("tenant_id", "default")
            ver = meta_copy.pop("version", 1)
            sch_ver = meta_copy.pop("schema_version", "1.0")

            event_store.store_event(
                aggregate_id=aggregate_id,
                aggregate_type=aggregate_type,
                event_type=event_type.value,
                metadata_payload=meta_copy,
                producer=prod,
                correlation_id=corr_id,
                causation_id=caus_id,
                tenant_id=ten_id,
                version=ver,
                schema_version=sch_ver,
            )
        except Exception as es_exc:
            log.warning("EventBus: EventStore save failed: %s", es_exc)

        # Call specific handlers
        for handler in self._handlers.get(event_type, []):
            try:
                handler(event)
            except Exception as exc:
                log.error("EventBus handler error for %s: %s", event_type, exc)

        # Call global handlers
        for handler in self._global_handlers:
            try:
                handler(event)
            except Exception as exc:
                log.error("EventBus global handler error: %s", exc)


# Singleton bus instance
bus = EventBus()
