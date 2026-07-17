from gtm_engine.events.event_bus import bus, EventBus, EventType, Event  # noqa: F401
from gtm_engine.events.domain_events import init_domain_events

try:
    init_domain_events()
except Exception:
    pass

