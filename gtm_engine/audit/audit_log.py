"""
audit/audit_log.py — Immutable structured audit log for every autonomous action.

Every entry records:
  timestamp   | agent   | action     | reason
  inputs      | outputs | duration_ms | success

Append-only: rows are never updated or deleted.
Exposed via GET /audit/log

Uses SQLAlchemy Core to align with CRM design.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generator

import sqlalchemy as sa
from sqlalchemy import Table, Column, Integer, String, Text, DateTime, Float

from gtm_engine.crm.db import engine, metadata

logger = logging.getLogger("gtm.audit_log")


# ── Schema ────────────────────────────────────────────────────────────────

audit_log_table = Table(
    "audit_log", metadata,
    Column("id",          Integer, primary_key=True, autoincrement=True),
    Column("timestamp",   DateTime, nullable=False),
    Column("agent",       String(128), nullable=False),
    Column("action",      String(128), nullable=False),
    Column("reason",      Text,        nullable=True),
    Column("lead_id",     Integer,     nullable=True),
    Column("inputs_json", Text,        nullable=True),
    Column("outputs_json", Text,        nullable=True),
    Column("duration_ms", Float,       nullable=True),
    Column("success",     Integer,     nullable=False, default=1),   # 1=OK, 0=FAIL
    Column("error",       Text,        nullable=True),
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_json(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, default=str)[:4000]   # cap at 4KB
    except (TypeError, ValueError):
        return str(obj)[:4000]


# ── Span context manager ──────────────────────────────────────────────────

@dataclass
class AuditSpan:
    """
    Collected inside an audit_log.span() context block.
    Set outputs via span.set_outputs(dict).
    """
    agent:   str
    action:  str
    reason:  str  = ""
    lead_id: int | None = None
    inputs:  Any  = None

    _start:    float = field(default_factory=time.monotonic, init=False)
    _outputs:  Any   = field(default=None, init=False)
    _error:    str | None = field(default=None, init=False)

    def set_outputs(self, outputs: Any) -> None:
        self._outputs = outputs

    def set_error(self, error: str) -> None:
        self._error = error


# ── Main logger ───────────────────────────────────────────────────────────

class AuditLog:
    def __init__(self) -> None:
        metadata.create_all(engine)

    # ── Write ──────────────────────────────────────────────────────────────

    def record(
        self,
        agent: str,
        action: str,
        *,
        reason:      str       = "",
        lead_id:     int | None = None,
        inputs:      Any       = None,
        outputs:     Any       = None,
        duration_ms: float | None = None,
        success:     bool      = True,
        error:       str | None = None,
    ) -> int:
        """Insert one audit entry. Returns the row ID."""
        with engine.begin() as conn:
            result = conn.execute(
                audit_log_table.insert().values(
                    timestamp    = _now(),
                    agent        = agent,
                    action       = action,
                    reason       = (reason or "")[:500],
                    lead_id      = lead_id,
                    inputs_json  = _to_json(inputs),
                    outputs_json = _to_json(outputs),
                    duration_ms  = duration_ms,
                    success      = 1 if success else 0,
                    error        = (error or "")[:2000] if error else None,
                )
            )
        return result.lastrowid

    @contextmanager
    def span(
        self,
        agent: str,
        action: str,
        *,
        reason:  str       = "",
        lead_id: int | None = None,
        inputs:  Any       = None,
    ) -> Generator[AuditSpan, None, None]:
        """
        Context manager that automatically records duration and success/failure.
        """
        span = AuditSpan(
            agent=agent, action=action, reason=reason, lead_id=lead_id, inputs=inputs,
        )
        try:
            yield span
            duration = (time.monotonic() - span._start) * 1000
            self.record(
                agent=agent, action=action,
                reason=reason, lead_id=lead_id,
                inputs=inputs, outputs=span._outputs,
                duration_ms=round(duration, 2),
                success=True,
            )
        except Exception as exc:
            duration = (time.monotonic() - span._start) * 1000
            error_str = str(exc)
            self.record(
                agent=agent, action=action,
                reason=reason, lead_id=lead_id,
                inputs=inputs, outputs=span._outputs,
                duration_ms=round(duration, 2),
                success=False,
                error=error_str,
            )
            raise

    # ── Read ───────────────────────────────────────────────────────────────

    def recent(self, limit: int = 100, lead_id: int | None = None) -> list[dict]:
        with engine.connect() as conn:
            q = audit_log_table.select().order_by(
                audit_log_table.c.timestamp.desc()
            ).limit(limit)
            if lead_id is not None:
                q = q.where(audit_log_table.c.lead_id == lead_id)
            rows = conn.execute(q).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def stats(self) -> dict:
        """Aggregate counts for API/dashboard."""
        with engine.connect() as conn:
            total = conn.execute(
                sa.select(sa.func.count()).select_from(audit_log_table)
            ).scalar() or 0
            failures = conn.execute(
                sa.select(sa.func.count()).select_from(audit_log_table)
                .where(audit_log_table.c.success == 0)
            ).scalar() or 0
            by_agent = conn.execute(
                sa.select(
                    audit_log_table.c.agent,
                    sa.func.count().label("calls"),
                    sa.func.avg(audit_log_table.c.duration_ms).label("avg_ms"),
                    sa.func.sum(
                        sa.case((audit_log_table.c.success == 0, 1), else_=0)
                    ).label("failures"),
                ).group_by(audit_log_table.c.agent)
            ).fetchall()
        return {
            "total_entries": total,
            "total_failures": failures,
            "by_agent": {
                r.agent: {
                    "calls": r.calls,
                    "avg_ms": round(r.avg_ms or 0, 1),
                    "failures": r.failures,
                }
                for r in by_agent
            },
        }

    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row._mapping)
        for jf in ("inputs_json", "outputs_json"):
            key = jf.replace("_json", "")
            try:
                d[key] = json.loads(d.pop(jf)) if d.get(jf) else None
            except (json.JSONDecodeError, TypeError):
                d[key] = d.pop(jf, None)
        if d.get("timestamp") and hasattr(d["timestamp"], "isoformat"):
            d["timestamp"] = d["timestamp"].isoformat()
        d["success"] = bool(d.get("success"))
        return d


# Singleton
audit_log = AuditLog()
