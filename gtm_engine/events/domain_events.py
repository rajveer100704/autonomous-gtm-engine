import logging
import datetime
from gtm_engine.events.event_bus import bus, EventType, Event
from gtm_engine.crm import db

log = logging.getLogger("gtm.domain_events")

def handle_email_sent(event: Event) -> None:
    outreach_id = event.metadata.get("outreach_id")
    if outreach_id:
        log.info("DomainEvent [handle_email_sent]: updating outreach status to 'sent' for id=%d", outreach_id)
        db.update_outreach_status(outreach_id, "sent", sent_at=datetime.datetime.now(datetime.timezone.utc))

    # Decoupled Memory Update
    if event.lead_id and event.metadata.get("subject"):
        from gtm_engine.memory.lead_memory import lead_memory
        lead_memory.update(
            event.lead_id,
            past_emails=[event.metadata.get("subject")]
        )

def handle_linkedin_sent(event: Event) -> None:
    outreach_id = event.metadata.get("outreach_id")
    if outreach_id:
        log.info("DomainEvent [handle_linkedin_sent]: updating outreach status to 'sent' for id=%d", outreach_id)
        db.update_outreach_status(outreach_id, "sent", sent_at=datetime.datetime.now(datetime.timezone.utc))

def handle_outreach_failed(event: Event) -> None:
    outreach_id = event.metadata.get("outreach_id")
    if outreach_id:
        log.warning("DomainEvent [handle_outreach_failed]: updating outreach status to 'failed' for id=%d", outreach_id)
        db.update_outreach_status(outreach_id, "failed")

def init_domain_events() -> None:
    """Initialize subscribers on the Event Bus and wire db_recorder."""
    from gtm_engine.services.crm_service import crm
    bus.db_recorder = crm.record_event

    bus.on(EventType.EMAIL_SENT, handle_email_sent)
    bus.on(EventType.LINKEDIN_SENT, handle_linkedin_sent)
    bus.on(EventType.EMAIL_FAILED, handle_outreach_failed)
    bus.on(EventType.LINKEDIN_FAILED, handle_outreach_failed)
    log.info("DomainEventBus initialized: decopled CRM, Memory, and Analytics subscribers registered.")
