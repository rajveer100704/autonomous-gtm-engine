from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import Table, Column, Integer, String, Text, DateTime

from gtm_engine.crm import db
metadata = db.metadata

logger = logging.getLogger("gtm.dead_letter_queue")

dead_letter_queue_table = Table(
    "dead_letter_queue", metadata,
    Column("id",              Integer, primary_key=True, autoincrement=True),
    Column("lead_id",         Integer, nullable=True),
    Column("task_type",       String(64),  nullable=False),
    Column("payload",         Text,        nullable=False),   # JSON
    Column("failure_reason",  Text,        nullable=False),
    Column("retry_count",      Integer,     default=0),
    Column("idempotency_key", String(128), nullable=True),
    Column("first_failed_at", DateTime,    nullable=False),
    Column("last_failed_at",  DateTime,    nullable=False),
    Column("status",          String(32),  default="pending_review"),  # pending_review | resolved | requeued
    extend_existing=True,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def init_dlq() -> None:
    """Create the DLQ table if it doesn't exist."""
    metadata.create_all(db.engine)


class DeadLetterQueue:
    """
    Immutable append — entries are never deleted, only status-updated.
    This creates a full audit trail of every execution failure.
    """

    def __init__(self) -> None:
        init_dlq()

    def add(
        self,
        lead_id: int | None,
        task: dict,
        failure_reason: str,
        retry_count: int = 0,
        idempotency_key: str | None = None,
    ) -> int:
        """Add a failed task to the DLQ. Returns the DLQ entry ID."""
        now = _now()
        with db.engine.begin() as conn:
            result = conn.execute(
                dead_letter_queue_table.insert().values(
                    lead_id         = lead_id,
                    task_type       = task.get("type", "unknown"),
                    payload         = json.dumps(task),
                    failure_reason  = failure_reason[:2000],   # cap long stack traces
                    retry_count     = retry_count,
                    idempotency_key = idempotency_key,
                    first_failed_at = now,
                    last_failed_at  = now,
                    status          = "pending_review",
                )
            )
            entry_id = result.lastrowid
        logger.error(
            "dlq: task moved to DLQ (id=%d lead=%s type=%s retries=%d): %s",
            entry_id, lead_id, task.get("type"), retry_count, failure_reason[:120],
        )
        return entry_id

    def pending(self) -> list[dict]:
        """Return all pending_review entries, newest first."""
        with db.engine.connect() as conn:
            rows = conn.execute(
                dead_letter_queue_table
                .select()
                .where(dead_letter_queue_table.c.status == "pending_review")
                .order_by(dead_letter_queue_table.c.last_failed_at.desc())
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def all_entries(self, limit: int = 100) -> list[dict]:
        """Return all DLQ entries (any status), newest first."""
        with db.engine.connect() as conn:
            rows = conn.execute(
                dead_letter_queue_table
                .select()
                .order_by(dead_letter_queue_table.c.last_failed_at.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, entry_id: int) -> dict | None:
        with db.engine.connect() as conn:
            row = conn.execute(
                dead_letter_queue_table
                .select()
                .where(dead_letter_queue_table.c.id == entry_id)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def count_by_status(self) -> dict[str, int]:
        with db.engine.connect() as conn:
            rows = conn.execute(
                sa.select(
                    dead_letter_queue_table.c.status,
                    sa.func.count().label("n"),
                ).group_by(dead_letter_queue_table.c.status)
            ).fetchall()
        return {r.status: r.n for r in rows}

    def resolve(self, entry_id: int, resolution_note: str = "") -> None:
        """Mark an entry as resolved (human decided to skip)."""
        self._set_status(entry_id, "resolved")
        logger.info("dlq: entry %d resolved: %s", entry_id, resolution_note)

    def mark_requeued(self, entry_id: int) -> None:
        """Mark that this entry was pushed back to the execution queue."""
        self._set_status(entry_id, "requeued")

    def _set_status(self, entry_id: int, status: str) -> None:
        with db.engine.begin() as conn:
            conn.execute(
                dead_letter_queue_table
                .update()
                .where(dead_letter_queue_table.c.id == entry_id)
                .values(status=status)
            )

    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row._mapping)
        try:
            d["payload"] = json.loads(d["payload"])
        except (json.JSONDecodeError, KeyError):
            pass
        for ts_field in ("first_failed_at", "last_failed_at"):
            if d.get(ts_field) and hasattr(d[ts_field], "isoformat"):
                d[ts_field] = d[ts_field].isoformat()
        return d


dead_letter_queue = DeadLetterQueue()
