import json
import uuid
import datetime
import logging
from typing import Any, Dict, List, Optional
import sqlalchemy as sa
from sqlalchemy import Table, Column, Integer, String, Text, DateTime
from gtm_engine.crm import db
metadata = db.metadata

logger = logging.getLogger("gtm.events.event_store")

# Define Event Store table
event_store_table = Table(
    "event_store", metadata,
    Column("event_id",          String(36), primary_key=True),  # UUID
    Column("aggregate_id",      String(64), nullable=False),
    Column("aggregate_type",    String(64), nullable=False),
    Column("event_type",        String(128), nullable=False),
    Column("version",            Integer, default=1),
    Column("metadata_json",     Text, nullable=False),
    Column("correlation_id",    String(36), nullable=True),
    Column("causation_id",      String(36), nullable=True),
    Column("timestamp",         DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
    Column("producer",          String(64), default="unknown"),
    Column("tenant_id",         String(64), default="default"),
    Column("schema_version",    String(10), default="1.0"),
    extend_existing=True,
)


def init_event_store() -> None:
    """Create the event store table if it doesn't exist."""
    metadata.create_all(db.engine)


class EventStore:
    def __init__(self):
        init_event_store()

    def store_event(
        self,
        aggregate_id: str,
        aggregate_type: str,
        event_type: str,
        metadata_payload: dict,
        producer: str = "unknown",
        correlation_id: str = None,
        causation_id: str = None,
        tenant_id: str = "default",
        version: int = 1,
        schema_version: str = "1.0",
    ) -> str:
        """Persist a new domain event into the Event Store."""
        event_id = str(uuid.uuid4())
        corr_id = correlation_id or str(uuid.uuid4())
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        with db.engine.begin() as conn:
            conn.execute(
                event_store_table.insert().values(
                    event_id=event_id,
                    aggregate_id=str(aggregate_id),
                    aggregate_type=aggregate_type,
                    event_type=event_type,
                    version=version,
                    metadata_json=json.dumps(metadata_payload),
                    correlation_id=corr_id,
                    causation_id=causation_id,
                    timestamp=now,
                    producer=producer,
                    tenant_id=tenant_id,
                    schema_version=schema_version,
                )
            )
        logger.debug("EventStore: stored event %s (%s)", event_id, event_type)
        return event_id

    def get_event(self, event_id: str) -> Optional[dict]:
        """Fetch a single event by ID."""
        with db.engine.connect() as conn:
            row = conn.execute(
                event_store_table.select().where(event_store_table.c.event_id == event_id)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    # ── Replay Filters ─────────────────────────────────────────────────────

    def replay_events(self, since_id: Optional[str] = None) -> List[dict]:
        """Replay all events, optionally starting *after* a specific event_id."""
        query = event_store_table.select().order_by(event_store_table.c.timestamp.asc())
        
        with db.engine.connect() as conn:
            if since_id:
                since_row = conn.execute(
                    event_store_table.select().where(event_store_table.c.event_id == since_id)
                ).fetchone()
                if since_row:
                    query = query.where(event_store_table.c.timestamp > since_row._mapping["timestamp"])
            
            rows = conn.execute(query).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def replay_until(self, event_id: str) -> List[dict]:
        """Replay all events occurred up to and including event_id."""
        with db.engine.connect() as conn:
            target = conn.execute(
                event_store_table.select().where(event_store_table.c.event_id == event_id)
            ).fetchone()
            if not target:
                return []
            
            rows = conn.execute(
                event_store_table.select()
                .where(event_store_table.c.timestamp <= target._mapping["timestamp"])
                .order_by(event_store_table.c.timestamp.asc())
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def replay_between(self, start: datetime.datetime, end: datetime.datetime) -> List[dict]:
        """Replay all events that occurred between start and end timestamps."""
        start_naive = start.replace(tzinfo=None) if start.tzinfo else start
        end_naive = end.replace(tzinfo=None) if end.tzinfo else end
        
        with db.engine.connect() as conn:
            rows = conn.execute(
                event_store_table.select()
                .where(event_store_table.c.timestamp.between(start_naive, end_naive))
                .order_by(event_store_table.c.timestamp.asc())
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def replay_by_type(self, event_type: str) -> List[dict]:
        """Filter events by event_type."""
        with db.engine.connect() as conn:
            rows = conn.execute(
                event_store_table.select()
                .where(event_store_table.c.event_type == event_type)
                .order_by(event_store_table.c.timestamp.asc())
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def replay_by_correlation(self, correlation_id: str) -> List[dict]:
        """Replay all events matching a specific correlation trace."""
        with db.engine.connect() as conn:
            rows = conn.execute(
                event_store_table.select()
                .where(event_store_table.c.correlation_id == correlation_id)
                .order_by(event_store_table.c.timestamp.asc())
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row._mapping)
        try:
            d["metadata"] = json.loads(d["metadata_json"])
        except Exception:
            d["metadata"] = {}
        if d.get("timestamp") and hasattr(d["timestamp"], "isoformat"):
            d["timestamp"] = d["timestamp"].isoformat()
        return d

# Global Event Store singleton
event_store = EventStore()
