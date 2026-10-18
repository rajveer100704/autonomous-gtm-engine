"""
CRM data layer. Uses SQLAlchemy Core so the SAME code runs against:
  - Postgres in production  (DATABASE_URL=postgresql+psycopg2://user:pass@host/db)
  - SQLite for local dev/testing (default, no server required)

All agents talk to this module only — never raw SQL elsewhere.
"""
import datetime
import re

from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Text, DateTime,
    ForeignKey, select, update, JSON, Float, UniqueConstraint
)

from gtm_engine.config import settings

engine = create_engine(settings.database_url, future=True)
metadata = MetaData()


def recreate_db_engine(url: str):
    """Recreate the global engine — used for isolating test runs to distinct DB files."""
    global engine
    try:
        engine.dispose()
    except Exception:
        pass
    engine = create_engine(url, future=True)
    try:
        from gtm_engine.queues.queue_provider import queue_provider
        queue_provider.reset()
    except Exception:
        pass


leads = Table(
    "leads", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("full_name", String),
    Column("title", String),
    Column("company", String),
    Column("domain", String),
    Column("email", String),
    Column("linkedin_url", String),
    Column("raw_apollo_json", JSON),
    Column("status", String, default="new"),
    Column("created_at", DateTime, default=datetime.datetime.utcnow),
)

research = Table(
    "research", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lead_id", Integer, ForeignKey("leads.id")),
    Column("company_summary", Text),
    Column("recent_signals", Text),
    Column("sources", Text, nullable=True),      # "title|url; title|url"
    Column("confidence", String, nullable=True), # low|medium|high — based on grounded signal count
    Column("created_at", DateTime, default=datetime.datetime.utcnow),
)

pain_points = Table(
    "pain_points", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lead_id", Integer, ForeignKey("leads.id")),
    Column("pain_points", Text),
    Column("confidence", String),
    Column("created_at", DateTime, default=datetime.datetime.utcnow),
)

outreach = Table(
    "outreach", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lead_id", Integer, ForeignKey("leads.id")),
    Column("channel", String),          # 'email' | 'linkedin'
    Column("sequence_step", Integer),   # 0 = first touch, 1 = follow-up 1, ...
    Column("subject", String),
    Column("body", Text),
    Column("variant", String, nullable=True),  # 'A' | 'B' — only set on step-0 email
    Column("scheduled_for", DateTime),
    Column("sent_at", DateTime),
    Column("status", String, default="drafted"),  # drafted -> scheduled -> sent -> replied/bounced
    Column("created_at", DateTime, default=datetime.datetime.utcnow),
)

ab_winner = Table(
    "ab_winner", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("test_name", String, default="email_copy_v1"),
    Column("variant", String),
    Column("metric", String),
    Column("value", String),
    Column("decided_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
)


research_cache = Table(
    "research_cache", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("domain", String, unique=True),
    Column("summary", Text),
    Column("signals", Text),
    Column("sources", Text, nullable=True),
    Column("confidence", String, nullable=True),
    Column("fetched_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
)


events = Table(
    "events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lead_id", Integer, ForeignKey("leads.id")),
    Column("event_type", String),  # lead_sourced | email_sent | linkedin_sent |
                                    # email_opened | email_replied | meeting_booked | won | lost
    Column("event_metadata", Text),
    Column("created_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
)

llm_usage = Table(
    "llm_usage", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lead_id", Integer, ForeignKey("leads.id"), nullable=True),
    Column("agent", String),          # 'research' | 'pain_point' | 'email' | 'linkedin' | 'followup'
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cost_usd", String),       # stored as string to avoid float rounding drift
    Column("created_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
)


reply_classifications = Table(
    "reply_classifications", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lead_id", Integer, ForeignKey("leads.id")),
    Column("classification", String),  # interested | meeting_request | needs_more_info |
                                         # not_now | out_of_office | not_interested | unsubscribe
    Column("confidence", String),
    Column("raw_reply", Text),
    Column("created_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
)


spans = Table(
    "spans", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("trace_id", String),      # groups every span from one pipeline run for one lead
    Column("lead_id", Integer, nullable=True),
    Column("agent", String),         # 'research' | 'pain_point' | 'email' | 'reflect' | 'linkedin' | ...
    Column("duration_ms", Integer),
    Column("success", Integer),      # 1/0 — sqlite/pg-portable boolean
    Column("error", Text, nullable=True),
    Column("created_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
)


learning_decisions = Table(
    "learning_decisions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("decision_key", String, unique=True, index=True),
    Column("lead_id", Integer, ForeignKey("leads.id")),
    Column("outreach_id", Integer, ForeignKey("outreach.id"), unique=True, nullable=True),
    Column("campaign_run_id", String, nullable=True),
    Column("channel", String, default="email"),
    Column("sequence_step", Integer, default=0),
    Column("decision_type", String, default="email_style"),
    Column("action", String),        # 'A' | 'B'
    Column("action_value", String),  # 'direct' | 'curiosity'
    Column("policy_name", String),   # 'randomized_baseline_v1', 'global_best_v1', 'segment_best_v1'
    Column("policy_version", String, default="1.0"),
    Column("assignment_probability", Float),  # 0 < p <= 1
    Column("segment_key", String, default="all"),
    Column("context_json", JSON, nullable=True),
    Column("created_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
)


learning_outcomes = Table(
    "learning_outcomes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_key", String, unique=True, index=True),
    Column("decision_id", Integer, ForeignKey("learning_decisions.id"), nullable=False),
    Column("outreach_id", Integer, ForeignKey("outreach.id"), nullable=False),
    Column("lead_id", Integer, ForeignKey("leads.id"), nullable=False),
    Column("event_type", String, nullable=False),
    Column("occurred_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
    Column("recorded_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
    Column("source", String, default="provider_webhook"),
    Column("is_simulated", Integer, default=0),
    Column("value_numeric", Float, nullable=True),
    Column("currency", String, nullable=True),
    Column("metadata_json", JSON, nullable=True),
)


# Only promotions written through the evidence-backed V5 gate can influence live assignment.
# The legacy ab_winner table remains for backward-compatible reporting.
learning_promotions = Table(
    "learning_promotions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("test_name", String, nullable=False),
    Column("variant", String, nullable=False),
    Column("policy_version", String, nullable=False),
    Column("evidence_key", String, unique=True, index=True, nullable=False),
    Column("decided_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
)


# Durable inbox for authenticated provider events. Only hashes and normalized
# metadata are stored; raw webhook bodies and email addresses are never persisted.
provider_event_receipts = Table(
    "provider_event_receipts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("provider", String, nullable=False),
    Column("provider_event_id", String, nullable=False),
    Column("provider_event_type", String, nullable=False),
    Column("payload_sha256", String, nullable=False),
    Column("lead_id", Integer, nullable=True),
    Column("outreach_id", Integer, nullable=True),
    Column("decision_id", Integer, nullable=True),
    Column("status", String, nullable=False, default="pending"),
    Column("error_code", String, nullable=True),
    Column("created_at", DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)),
    Column("processed_at", DateTime, nullable=True),
    UniqueConstraint("provider", "provider_event_id", name="uq_provider_event_receipt"),
)


def init_db():
    # Additive schema initialization: existing data/tables are preserved.
    # New tables, including provider_event_receipts, are created on upgrade.
    metadata.create_all(engine)


def create_or_get_provider_receipt(
    provider: str, provider_event_id: str, provider_event_type: str, payload_sha256: str,
    lead_id: int | None = None, outreach_id: int | None = None, decision_id: int | None = None,
) -> dict:
    """Durably register a provider event before processing it; safe for retries.

    A pending/failed receipt can be resumed after a process crash. The unique
    provider/event key prevents duplicate receipt rows across concurrent retries.
    """
    if not provider or not provider_event_id:
        raise ValueError("provider and provider_event_id are required")
    values = {
        "provider": provider, "provider_event_id": provider_event_id,
        "provider_event_type": provider_event_type or "unknown", "payload_sha256": payload_sha256,
        "lead_id": lead_id, "outreach_id": outreach_id, "decision_id": decision_id,
        "status": "pending", "created_at": datetime.datetime.now(datetime.timezone.utc),
    }
    with engine.begin() as conn:
        existing = conn.execute(
            select(provider_event_receipts).where(
                provider_event_receipts.c.provider == provider,
                provider_event_receipts.c.provider_event_id == provider_event_id,
            ).with_for_update()
        ).mappings().first()
        if existing:
            return {"receipt": dict(existing), "created": False}

        dialect = conn.dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            dialect_insert = None
        if dialect_insert:
            result = conn.execute(
                dialect_insert(provider_event_receipts).values(**values)
                .on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
            )
            created = bool(result.rowcount)
        else:
            try:
                conn.execute(provider_event_receipts.insert().values(**values))
                created = True
            except Exception:
                # Non-PostgreSQL/SQLite fallback; re-query after the insert conflict.
                created = False
        row = conn.execute(
            select(provider_event_receipts).where(
                provider_event_receipts.c.provider == provider,
                provider_event_receipts.c.provider_event_id == provider_event_id,
            ).with_for_update()
        ).mappings().first()
        if row is None:
            raise RuntimeError("provider event receipt insert did not persist")
        return {"receipt": dict(row), "created": created}


def update_provider_receipt(receipt_id: int, status: str, error_code: str | None = None) -> None:
    if status not in {"pending", "processed", "ignored", "failed"}:
        raise ValueError(f"Invalid provider receipt status: {status}")
    now = datetime.datetime.now(datetime.timezone.utc)
    values = {"status": status, "error_code": error_code}
    if status in {"processed", "ignored"}:
        values["processed_at"] = now
    with engine.begin() as conn:
        conn.execute(
            update(provider_event_receipts).where(provider_event_receipts.c.id == receipt_id).values(**values)
        )


def list_provider_receipts(provider: str = "sendgrid") -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            select(provider_event_receipts).where(provider_event_receipts.c.provider == provider)
            .order_by(provider_event_receipts.c.id.asc())
        ).mappings().all()
        return [dict(row) for row in rows]


def insert_lead(lead: dict) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            leads.insert().values(
                full_name=lead.get("full_name"),
                title=lead.get("title"),
                company=lead.get("company"),
                domain=lead.get("domain"),
                email=lead.get("email"),
                linkedin_url=lead.get("linkedin_url"),
                raw_apollo_json=lead.get("raw", {}),
                status="new",
                created_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        return result.inserted_primary_key[0]


def save_research(lead_id: int, summary: str, signals: str, sources: str = "", confidence: str = ""):
    with engine.begin() as conn:
        conn.execute(research.insert().values(
            lead_id=lead_id, company_summary=summary, recent_signals=signals,
            sources=sources, confidence=confidence,
            created_at=datetime.datetime.now(datetime.timezone.utc),
        ))


def save_pain_points(lead_id: int, pain_points_text: str, confidence: str):
    with engine.begin() as conn:
        conn.execute(pain_points.insert().values(
            lead_id=lead_id, pain_points=pain_points_text, confidence=confidence,
            created_at=datetime.datetime.now(datetime.timezone.utc),
        ))


def save_outreach(lead_id: int, channel: str, step: int, subject: str, body: str,
                   scheduled_for, status: str = "drafted", variant: str = None) -> int:
    with engine.begin() as conn:
        result = conn.execute(outreach.insert().values(
            lead_id=lead_id, channel=channel, sequence_step=step, subject=subject,
            body=body, variant=variant, scheduled_for=scheduled_for, status=status,
            created_at=datetime.datetime.now(datetime.timezone.utc),
        ))
        return result.inserted_primary_key[0]


def update_lead_status(lead_id: int, status: str):
    with engine.begin() as conn:
        conn.execute(update(leads).where(leads.c.id == lead_id).values(status=status))


def update_outreach_status(outreach_id: int, status: str, sent_at=None):
    with engine.begin() as conn:
        values = {"status": status}
        if sent_at:
            values["sent_at"] = sent_at
        conn.execute(update(outreach).where(outreach.c.id == outreach_id).values(**values))


def due_followups(now: datetime.datetime):
    with engine.begin() as conn:
        rows = conn.execute(
            select(outreach).where(outreach.c.status == "scheduled",
                                    outreach.c.scheduled_for <= now)
        ).mappings().all()
        return [dict(r) for r in rows]


def get_cached_research(domain: str, max_age_hours: int):
    if not domain:
        return None
    with engine.begin() as conn:
        row = conn.execute(
            select(research_cache).where(research_cache.c.domain == domain)
        ).mappings().first()
    if not row:
        return None
    fetched_at = row["fetched_at"]
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=datetime.timezone.utc)
    age = datetime.datetime.now(datetime.timezone.utc) - fetched_at
    if age > datetime.timedelta(hours=max_age_hours):
        return None
    return {"summary": row["summary"], "signals": row["signals"],
            "sources": row["sources"] or "", "confidence": row["confidence"] or ""}


def upsert_research_cache(domain: str, summary: str, signals: str, sources: str = "", confidence: str = ""):
    if not domain:
        return
    with engine.begin() as conn:
        existing = conn.execute(
            select(research_cache.c.id).where(research_cache.c.domain == domain)
        ).first()
        now = datetime.datetime.now(datetime.timezone.utc)
        if existing:
            conn.execute(
                update(research_cache).where(research_cache.c.domain == domain)
                .values(summary=summary, signals=signals, sources=sources,
                        confidence=confidence, fetched_at=now)
            )
        else:
            conn.execute(
                research_cache.insert().values(
                    domain=domain, summary=summary, signals=signals,
                    sources=sources, confidence=confidence, fetched_at=now
                )
            )


def record_event(lead_id: int, event_type: str, metadata: str = ""):
    with engine.begin() as conn:
        conn.execute(events.insert().values(
            lead_id=lead_id, event_type=event_type, event_metadata=metadata,
            created_at=datetime.datetime.now(datetime.timezone.utc),
        ))


def all_events():
    with engine.begin() as conn:
        rows = conn.execute(select(events).order_by(events.c.id.desc())).mappings().all()
        return [dict(r) for r in rows]


def funnel_counts():
    """
    Distinct leads that have reached each funnel stage. A lead can only be
    counted once per stage even if the event fired multiple times (e.g. two
    opens on the same email).
    """
    stages = ["lead_sourced", "email_sent", "email_opened", "email_replied",
              "meeting_booked", "won"]
    with engine.begin() as conn:
        counts = {}
        for stage in stages:
            result = conn.execute(
                select(events.c.lead_id).where(events.c.event_type == stage).distinct()
            ).all()
            counts[stage] = len(result)
        return counts


def record_llm_usage(agent: str, input_tokens: int, output_tokens: int, cost_usd: float,
                      lead_id: int = None):
    with engine.begin() as conn:
        conn.execute(llm_usage.insert().values(
            lead_id=lead_id, agent=agent, input_tokens=input_tokens,
            output_tokens=output_tokens, cost_usd=str(cost_usd),
            created_at=datetime.datetime.now(datetime.timezone.utc),
        ))


def cost_summary():
    with engine.begin() as conn:
        rows = conn.execute(select(llm_usage)).mappings().all()
    by_agent = {}
    total_cost = 0.0
    total_input = 0
    total_output = 0
    for r in rows:
        agent = r["agent"]
        cost = float(r["cost_usd"] or 0)
        by_agent.setdefault(agent, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        by_agent[agent]["calls"] += 1
        by_agent[agent]["input_tokens"] += r["input_tokens"] or 0
        by_agent[agent]["output_tokens"] += r["output_tokens"] or 0
        by_agent[agent]["cost_usd"] += cost
        total_cost += cost
        total_input += r["input_tokens"] or 0
        total_output += r["output_tokens"] or 0
    return {
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "by_agent": by_agent,
    }


def cost_per_lead():
    with engine.begin() as conn:
        rows = conn.execute(select(llm_usage).where(llm_usage.c.lead_id.isnot(None))).mappings().all()
    by_lead = {}
    for r in rows:
        by_lead.setdefault(r["lead_id"], 0.0)
        by_lead[r["lead_id"]] += float(r["cost_usd"] or 0)
    if not by_lead:
        return 0.0
    return round(sum(by_lead.values()) / len(by_lead), 6)


def get_lead(lead_id: int):
    with engine.begin() as conn:
        row = conn.execute(select(leads).where(leads.c.id == lead_id)).mappings().first()
        return dict(row) if row else None


def cancel_scheduled_outreach(lead_id: int) -> int:
    """Cancels all still-pending (status='scheduled') outreach for a lead. Returns count affected."""
    with engine.begin() as conn:
        rows = conn.execute(
            select(outreach.c.id).where(outreach.c.lead_id == lead_id, outreach.c.status == "scheduled")
        ).all()
        if rows:
            conn.execute(
                update(outreach).where(outreach.c.lead_id == lead_id, outreach.c.status == "scheduled")
                .values(status="cancelled")
            )
        return len(rows)


def reschedule_scheduled_outreach(lead_id: int, delta_days: int) -> int:
    """Pushes all still-pending outreach for a lead out by delta_days. Returns count affected."""
    with engine.begin() as conn:
        rows = conn.execute(
            select(outreach).where(outreach.c.lead_id == lead_id, outreach.c.status == "scheduled")
        ).mappings().all()
        for row in rows:
            new_time = row["scheduled_for"] + datetime.timedelta(days=delta_days)
            conn.execute(
                update(outreach).where(outreach.c.id == row["id"]).values(scheduled_for=new_time)
            )
        return len(rows)


def save_reply_classification(lead_id: int, classification: str, confidence: str, raw_reply: str):
    with engine.begin() as conn:
        conn.execute(reply_classifications.insert().values(
            lead_id=lead_id, classification=classification, confidence=confidence,
            raw_reply=raw_reply, created_at=datetime.datetime.now(datetime.timezone.utc),
        ))


def reply_classification_counts():
    with engine.begin() as conn:
        rows = conn.execute(select(reply_classifications.c.classification)).all()
    counts = {}
    for (c,) in rows:
        counts[c] = counts.get(c, 0) + 1
    return counts


def lead_variant_map():
    """lead_id -> variant, derived from each lead's step-0 email outreach."""
    with engine.begin() as conn:
        rows = conn.execute(
            select(outreach.c.lead_id, outreach.c.variant)
            .where(outreach.c.channel == "email", outreach.c.sequence_step == 0,
                   outreach.c.variant.isnot(None))
        ).all()
    return {lead_id: variant for lead_id, variant in rows}


def variant_performance():
    """
    Per-variant funnel: how many leads were SENT that variant vs. how many
    distinct leads opened / replied / booked a meeting / won. Distinct-lead
    counting mirrors funnel_counts() so repeat opens don't inflate a rate.
    """
    lv = lead_variant_map()
    variants = sorted(set(lv.values()))
    sets = {v: {"sent": set(), "email_opened": set(), "email_replied": set(),
                "meeting_booked": set(), "won": set()} for v in variants}

    for lead_id, variant in lv.items():
        sets[variant]["sent"].add(lead_id)

    for e in all_events():
        variant = lv.get(e["lead_id"])
        if not variant or e["event_type"] not in sets[variant]:
            continue
        sets[variant][e["event_type"]].add(e["lead_id"])

    performance = {}
    for v in variants:
        sent = len(sets[v]["sent"])
        opened = len(sets[v]["email_opened"])
        replied = len(sets[v]["email_replied"])
        meetings = len(sets[v]["meeting_booked"])
        won = len(sets[v]["won"])
        performance[v] = {
            "sent": sent, "opened": opened, "replied": replied,
            "meetings": meetings, "won": won,
            "open_rate": round(opened / sent, 4) if sent else 0.0,
            "reply_rate": round(replied / sent, 4) if sent else 0.0,
            "meeting_rate": round(meetings / sent, 4) if sent else 0.0,
        }
    return performance


def get_active_winner(test_name: str = "email_copy_v1"):
    with engine.begin() as conn:
        row = conn.execute(
            select(ab_winner).where(ab_winner.c.test_name == test_name)
            .order_by(ab_winner.c.id.desc())
        ).mappings().first()
    return dict(row) if row else None


def set_winner(variant: str, metric: str, value: float, test_name: str = "email_copy_v1"):
    with engine.begin() as conn:
        conn.execute(ab_winner.insert().values(
            test_name=test_name, variant=variant, metric=metric, value=str(value),
            decided_at=datetime.datetime.now(datetime.timezone.utc),
        ))



def record_validated_promotion(test_name: str, variant: str, policy_version: str, evidence_key: str) -> bool:
    """Persist a V5-gate-approved promotion idempotently; returns True if newly added."""
    if variant not in ("A", "B"):
        raise ValueError("variant must be A or B")
    if not evidence_key:
        raise ValueError("evidence_key is required")
    with engine.begin() as conn:
        existing = conn.execute(select(learning_promotions).where(learning_promotions.c.evidence_key == evidence_key)).mappings().first()
        if existing:
            if existing["test_name"] != test_name or existing["variant"] != variant:
                raise ValueError("evidence_key reused for a different promotion")
            return False
        conn.execute(learning_promotions.insert().values(
            test_name=test_name, variant=variant, policy_version=policy_version,
            evidence_key=evidence_key, decided_at=datetime.datetime.now(datetime.timezone.utc)))
        return True


def get_validated_active_winner(test_name: str = "email_copy_v1"):
    """Return only a winner recorded after passing the V5 evidence gate."""
    with engine.begin() as conn:
        row = conn.execute(select(learning_promotions).where(learning_promotions.c.test_name == test_name)
                           .order_by(learning_promotions.c.id.desc())).mappings().first()
    return dict(row) if row else None


def record_span(trace_id: str, agent: str, duration_ms: int, success: bool,
                 error: str = None, lead_id: int = None):
    with engine.begin() as conn:
        conn.execute(spans.insert().values(
            trace_id=trace_id, lead_id=lead_id, agent=agent, duration_ms=duration_ms,
            success=1 if success else 0, error=error,
            created_at=datetime.datetime.now(datetime.timezone.utc),
        ))


def trace_for_lead(lead_id: int):
    with engine.begin() as conn:
        rows = conn.execute(
            select(spans).where(spans.c.lead_id == lead_id).order_by(spans.c.id.asc())
        ).mappings().all()
        return [dict(r) for r in rows]


def latency_summary():
    with engine.begin() as conn:
        rows = conn.execute(select(spans)).mappings().all()
    by_agent = {}
    for r in rows:
        agent = r["agent"]
        by_agent.setdefault(agent, {"calls": 0, "total_ms": 0, "failures": 0, "max_ms": 0})
        by_agent[agent]["calls"] += 1
        by_agent[agent]["total_ms"] += r["duration_ms"] or 0
        by_agent[agent]["max_ms"] = max(by_agent[agent]["max_ms"], r["duration_ms"] or 0)
        if not r["success"]:
            by_agent[agent]["failures"] += 1
    for agent, stats in by_agent.items():
        stats["avg_ms"] = round(stats["total_ms"] / stats["calls"], 1) if stats["calls"] else 0
    return by_agent


def all_research_cache():
    with engine.begin() as conn:
        rows = conn.execute(select(research_cache).order_by(research_cache.c.fetched_at.desc())).mappings().all()
        return [dict(r) for r in rows]


def company_summary():
    """One row per distinct company, aggregated from its leads."""
    ls = all_leads()
    by_company = {}
    for lead in ls:
        key = lead["company"] or lead["domain"] or f"lead-{lead['id']}"
        entry = by_company.setdefault(key, {
            "company": lead["company"], "domain": lead["domain"], "lead_count": 0,
            "statuses": [],
        })
        entry["lead_count"] += 1
        entry["statuses"].append(lead["status"])
    for entry in by_company.values():
        entry["latest_status"] = entry["statuses"][-1] if entry["statuses"] else None
        del entry["statuses"]
    return list(by_company.values())


def lead_detail(lead_id: int):
    lead = get_lead(lead_id)
    if not lead:
        return None
    with engine.begin() as conn:
        research_row = conn.execute(
            select(research).where(research.c.lead_id == lead_id).order_by(research.c.id.desc())
        ).mappings().first()
        pain_row = conn.execute(
            select(pain_points).where(pain_points.c.lead_id == lead_id).order_by(pain_points.c.id.desc())
        ).mappings().first()
        outreach_rows = conn.execute(
            select(outreach).where(outreach.c.lead_id == lead_id).order_by(outreach.c.id.asc())
        ).mappings().all()
        event_rows = conn.execute(
            select(events).where(events.c.lead_id == lead_id).order_by(events.c.id.asc())
        ).mappings().all()
        reply_rows = conn.execute(
            select(reply_classifications).where(reply_classifications.c.lead_id == lead_id)
            .order_by(reply_classifications.c.id.desc())
        ).mappings().all()
    return {
        "lead": lead,
        "research": dict(research_row) if research_row else None,
        "pain_points": dict(pain_row) if pain_row else None,
        "outreach": [dict(r) for r in outreach_rows],
        "events": [dict(r) for r in event_rows],
        "reply_classifications": [dict(r) for r in reply_rows],
        "trace": trace_for_lead(lead_id),
    }


def all_leads():
    with engine.begin() as conn:
        rows = conn.execute(select(leads).order_by(leads.c.id.desc())).mappings().all()
        return [dict(r) for r in rows]


def all_outreach():
    with engine.begin() as conn:
        rows = conn.execute(select(outreach).order_by(outreach.c.id.desc())).mappings().all()
        return [dict(r) for r in rows]


def campaign_analytics():
    """
    Rich GTM Campaign Analytics.
    Calculates:
      - Funnel metrics: Sourced -> Sent -> Delivered -> Opened -> Replied -> Meeting Booked -> Won -> Conversion rates
      - Average Reply Time & Average Meeting Booking Time
      - Top Performing Templates (A vs B)
      - Top Industries and Top Personas (by volume and reply rate)
    """
    with engine.begin() as conn:
        evs = conn.execute(select(events)).mappings().all()
        lds = conn.execute(select(leads)).mappings().all()
        outs = conn.execute(select(outreach)).mappings().all()
        replies = conn.execute(select(reply_classifications)).mappings().all()

    # 1. Funnel and Conversion
    funnel = {
        "sourced": len(lds),
        "sent": len([o for o in outs if o["status"] in ("sent", "replied")]),
        "delivered": len([o for o in outs if o["status"] in ("sent", "replied")]),  # Mock delivery rate = 100%
        "opened": len(set(e["lead_id"] for e in evs if e["event_type"] == "email_opened")),
        "replies": len(set(e["lead_id"] for e in evs if e["event_type"] in ("email_replied", "linkedin_replied"))),
        "meetings": len(set(e["lead_id"] for e in evs if e["event_type"] == "meeting_booked")),
        "won": len(set(e["lead_id"] for e in evs if e["event_type"] == "won")),
    }

    sent = funnel["sent"]
    funnel_rates = {
        "delivered_rate": 100.0 if sent > 0 else 0.0,
        "open_rate": round((funnel["opened"] / sent) * 100, 1) if sent > 0 else 0.0,
        "reply_rate": round((funnel["replies"] / sent) * 100, 1) if sent > 0 else 0.0,
        "meeting_rate": round((funnel["meetings"] / sent) * 100, 1) if sent > 0 else 0.0,
        "won_rate": round((funnel["won"] / sent) * 100, 1) if sent > 0 else 0.0,
    }

    # 2. Avg Reply / Booking Times (in hours)
    reply_times = []
    meeting_times = []
    
    # Map sent times per lead
    sent_times = {}
    for o in outs:
        if o["status"] in ("sent", "replied") and o["sent_at"]:
            # Keep earliest sent time
            lead_id = o["lead_id"]
            if lead_id not in sent_times or o["sent_at"] < sent_times[lead_id]:
                sent_times[lead_id] = o["sent_at"]

    for r in replies:
        lead_id = r["lead_id"]
        if lead_id in sent_times:
            sent_at = sent_times[lead_id]
            replied_at = r["created_at"]
            if replied_at > sent_at:
                diff_hours = (replied_at - sent_at).total_seconds() / 3600.0
                reply_times.append(diff_hours)

    for e in evs:
        if e["event_type"] == "meeting_booked":
            lead_id = e["lead_id"]
            if lead_id in sent_times:
                sent_at = sent_times[lead_id]
                booked_at = e["created_at"]
                if booked_at > sent_at:
                    diff_hours = (booked_at - sent_at).total_seconds() / 3600.0
                    meeting_times.append(diff_hours)

    avg_reply_time = round(sum(reply_times) / len(reply_times), 1) if reply_times else 0.0
    avg_meeting_time = round(sum(meeting_times) / len(meeting_times), 1) if meeting_times else 0.0

    # 3. Top Performing Templates
    template_stats = {}
    for o in outs:
        v = o["variant"] or "default"
        if o["sequence_step"] == 0 and o["channel"] == "email":
            stat = template_stats.setdefault(v, {"sent": 0, "opened": 0, "replied": 0})
            stat["sent"] += 1

    # Match events to templates
    for e in evs:
        lead_id = e["lead_id"]
        # Find step 0 email variant for this lead
        lead_outs = [o for o in outs if o["lead_id"] == lead_id and o["sequence_step"] == 0 and o["channel"] == "email"]
        if lead_outs:
            v = lead_outs[0]["variant"] or "default"
            if v in template_stats:
                if e["event_type"] == "email_opened":
                    template_stats[v]["opened"] += 1
                elif e["event_type"] == "email_replied":
                    template_stats[v]["replied"] += 1

    for v, s in template_stats.items():
        s["open_rate"] = round((s["opened"] / s["sent"]) * 100, 1) if s["sent"] > 0 else 0.0
        s["reply_rate"] = round((s["replied"] / s["sent"]) * 100, 1) if s["sent"] > 0 else 0.0

    # 4. Top Industries
    industry_stats = {}
    for l in lds:
        raw_json = l["raw_apollo_json"] or {}
        if isinstance(raw_json, str):
            import json
            try:
                raw_json = json.loads(raw_json)
            except Exception:
                raw_json = {}
        ind = raw_json.get("industry") or "Unknown"
        stat = industry_stats.setdefault(ind, {"sourced": 0, "replied": 0})
        stat["sourced"] += 1
        
        # Check if lead replied
        has_replied = any(e["lead_id"] == l["id"] and e["event_type"] in ("email_replied", "linkedin_replied") for e in evs)
        if has_replied:
            stat["replied"] += 1

    for ind, s in industry_stats.items():
        s["reply_rate"] = round((s["replied"] / s["sourced"]) * 100, 1) if s["sourced"] > 0 else 0.0

    # 5. Top Personas
    persona_stats = {}
    for l in lds:
        title = l["title"] or "Unknown"
        # Normalize title a bit
        norm_title = "Founder/CEO" if any(x in title.lower() for x in ("founder", "ceo", "president")) else (
            "Sales/Growth Leader" if any(x in title.lower() for x in ("sales", "growth", "marketing")) else "Other"
        )
        stat = persona_stats.setdefault(norm_title, {"sourced": 0, "replied": 0})
        stat["sourced"] += 1
        has_replied = any(e["lead_id"] == l["id"] and e["event_type"] in ("email_replied", "linkedin_replied") for e in evs)
        if has_replied:
            stat["replied"] += 1

    for pers, s in persona_stats.items():
        s["reply_rate"] = round((s["replied"] / s["sourced"]) * 100, 1) if s["sourced"] > 0 else 0.0

    return {
        "funnel": funnel,
        "rates": funnel_rates,
        "avg_reply_time_hours": avg_reply_time,
        "avg_meeting_time_hours": avg_meeting_time,
        "templates": template_stats,
        "industries": dict(sorted(industry_stats.items(), key=lambda x: x[1]["sourced"], reverse=True)[:5]),
        "personas": persona_stats,
    }


# ── V5 Outcome Learning & Explicit Attribution ─────────────────────────────
SUPPORTED_OUTCOMES = {
    "email_replied", "meeting_booked", "won", "lost",
    "bounced", "unsubscribe", "email_opened"
}


def _ensure_utc(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)



def record_decision(
    lead_id: int, action: str, action_value: str, assignment_probability: float,
    policy_name: str, policy_version: str = "1.0", decision_key: str | None = None,
    outreach_id: int | None = None, campaign_run_id: str | None = None,
    channel: str = "email", sequence_step: int = 0, decision_type: str = "email_style",
    segment_key: str = "all", context: dict | None = None,
    created_at: datetime.datetime | None = None,
) -> int:
    """Persist a decision after outreach exists, with checked idempotency and ownership."""
    if action not in ("A", "B"):
        raise ValueError("action must be 'A' or 'B'")
    if not (0.0 < float(assignment_probability) <= 1.0):
        raise ValueError("assignment_probability must be in (0, 1]")
    ctx = context or {}
    if not isinstance(ctx, dict):
        raise ValueError("context must be a dictionary")
    if not decision_key:
        decision_key = f"dec_{lead_id}_{channel}_{sequence_step}_{outreach_id if outreach_id is not None else 'pending'}"
    ts = _ensure_utc(created_at) or datetime.datetime.now(datetime.timezone.utc)

    with engine.begin() as conn:
        lead = conn.execute(select(leads.c.id).where(leads.c.id == lead_id)).first()
        if not lead:
            raise ValueError(f"Lead ID {lead_id} not found")
        existing = conn.execute(select(learning_decisions).where(learning_decisions.c.decision_key == decision_key)).mappings().first()
        if existing:
            same = (existing["lead_id"] == lead_id and existing["outreach_id"] == outreach_id
                    and existing["action"] == action and existing["policy_name"] == policy_name)
            if not same:
                raise ValueError("decision_key was already used for a different decision")
            return existing["id"]
        outreach_row = None
        if outreach_id is not None:
            outreach_row = conn.execute(select(outreach).where(outreach.c.id == outreach_id)).mappings().first()
            if not outreach_row:
                raise ValueError(f"Outreach ID {outreach_id} not found")
            if outreach_row["lead_id"] != lead_id:
                raise ValueError("Decision lead_id does not match outreach owner")
            if channel and outreach_row["channel"] != channel:
                raise ValueError("Decision channel does not match outreach channel")
            if sequence_step is not None and outreach_row["sequence_step"] != sequence_step:
                raise ValueError("Decision sequence_step does not match outreach sequence_step")
            if outreach_row["variant"] not in (None, action):
                raise ValueError("Decision action does not match outreach variant")

        if outreach_id is not None:
            existing_outreach = conn.execute(select(learning_decisions).where(learning_decisions.c.outreach_id == outreach_id)).mappings().first()
            if existing_outreach:
                if existing_outreach["lead_id"] == lead_id and existing_outreach["action"] == action:
                    return existing_outreach["id"]
                raise ValueError("Outreach already has a different learning decision")

        result = conn.execute(learning_decisions.insert().values(
            decision_key=decision_key, lead_id=lead_id, outreach_id=outreach_id,
            campaign_run_id=campaign_run_id, channel=channel, sequence_step=sequence_step,
            decision_type=decision_type, action=action, action_value=action_value,
            policy_name=policy_name, policy_version=policy_version,
            assignment_probability=float(assignment_probability), segment_key=segment_key or "all",
            context_json=ctx, created_at=ts,
        ))
        return result.inserted_primary_key[0]

def get_decision(decision_id: int) -> dict | None:
    with engine.begin() as conn:
        row = conn.execute(
            select(learning_decisions).where(learning_decisions.c.id == decision_id)
        ).mappings().first()
        return dict(row) if row else None


def get_decision_by_key(decision_key: str) -> dict | None:
    with engine.begin() as conn:
        row = conn.execute(
            select(learning_decisions).where(learning_decisions.c.decision_key == decision_key)
        ).mappings().first()
        return dict(row) if row else None


def get_decision_for_outreach(outreach_id: int) -> dict | None:
    with engine.begin() as conn:
        row = conn.execute(
            select(learning_decisions).where(learning_decisions.c.outreach_id == outreach_id)
        ).mappings().first()
        return dict(row) if row else None


def all_learning_decisions() -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            select(learning_decisions).order_by(learning_decisions.c.id.asc())
        ).mappings().all()
        return [dict(r) for r in rows]



def record_learning_outcome(
    lead_id: int, outreach_id: int, decision_id: int, event_type: str,
    event_key: str | None = None, occurred_at: datetime.datetime | None = None,
    source: str = "provider_webhook", is_simulated: bool = False,
    value_numeric: float | None = None, currency: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Record an idempotent outcome only against its exact decision/outreach pair."""
    if event_type not in SUPPORTED_OUTCOMES:
        raise ValueError(f"Unsupported outcome event_type: {event_type!r}")
    occ_ts = _ensure_utc(occurred_at) or datetime.datetime.now(datetime.timezone.utc)
    rec_ts = datetime.datetime.now(datetime.timezone.utc)
    if not event_key:
        # Outcome metrics are binary per decision/event type; a stable default avoids retry duplicates.
        event_key = f"auto:{decision_id}:{event_type}"
    with engine.begin() as conn:
        out_row = conn.execute(select(outreach).where(outreach.c.id == outreach_id)).mappings().first()
        dec_row = conn.execute(select(learning_decisions).where(learning_decisions.c.id == decision_id)).mappings().first()
        if not out_row:
            raise ValueError(f"Outreach ID {outreach_id} not found")
        if not dec_row:
            raise ValueError(f"Decision ID {decision_id} not found")
        if out_row["lead_id"] != lead_id or dec_row["lead_id"] != lead_id:
            raise ValueError("Cross-lead mismatch among lead, outreach, and decision")
        if dec_row["outreach_id"] != outreach_id:
            raise ValueError("Learning outcome must reference the decision's exact outreach")
        if out_row["channel"] != "email" or out_row["sequence_step"] != 0 or dec_row["decision_type"] != "email_style":
            raise ValueError("Outcome is not linked to an eligible first-touch email decision")
        if out_row["status"] not in ("sent", "replied", "bounced") or out_row["sent_at"] is None:
            raise ValueError("Outcome cannot be attributed to an outreach that was not actually marked sent")
        existing = conn.execute(select(learning_outcomes).where(learning_outcomes.c.event_key == event_key)).mappings().first()
        if existing:
            same = (existing["decision_id"] == decision_id and existing["outreach_id"] == outreach_id
                    and existing["lead_id"] == lead_id and existing["event_type"] == event_type)
            if not same:
                raise ValueError("event_key was already used for a different outcome")
            return {"status": "duplicate", "outcome_id": existing["id"], "event_key": event_key,
                    "event_type": existing["event_type"], "lead_id": lead_id,
                    "outreach_id": outreach_id, "decision_id": decision_id}
        values = dict(
            event_key=event_key, decision_id=decision_id, outreach_id=outreach_id,
            lead_id=lead_id, event_type=event_type, occurred_at=occ_ts, recorded_at=rec_ts,
            source=source, is_simulated=1 if is_simulated else 0,
            value_numeric=value_numeric, currency=currency, metadata_json=metadata or {},
        )
        # ON CONFLICT closes the SELECT-then-INSERT race when a provider retries
        # the same event concurrently on PostgreSQL or SQLite.
        if conn.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        elif conn.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            dialect_insert = None
        if dialect_insert:
            res = conn.execute(
                dialect_insert(learning_outcomes).values(**values)
                .on_conflict_do_nothing(index_elements=["event_key"])
            )
            if res.rowcount:
                return {"status": "recorded", "outcome_id": res.inserted_primary_key[0], "event_key": event_key,
                        "event_type": event_type, "lead_id": lead_id, "outreach_id": outreach_id,
                        "decision_id": decision_id}
            existing = conn.execute(
                select(learning_outcomes).where(learning_outcomes.c.event_key == event_key)
            ).mappings().first()
            if existing is None:
                raise RuntimeError("outcome conflict occurred but existing event could not be loaded")
            same = (existing["decision_id"] == decision_id and existing["outreach_id"] == outreach_id
                    and existing["lead_id"] == lead_id and existing["event_type"] == event_type)
            if not same:
                raise ValueError("event_key was already used for a different outcome")
            return {"status": "duplicate", "outcome_id": existing["id"], "event_key": event_key,
                    "event_type": existing["event_type"], "lead_id": lead_id,
                    "outreach_id": outreach_id, "decision_id": decision_id}
        res = conn.execute(learning_outcomes.insert().values(**values))
        return {"status": "recorded", "outcome_id": res.inserted_primary_key[0], "event_key": event_key,
                "event_type": event_type, "lead_id": lead_id, "outreach_id": outreach_id,
                "decision_id": decision_id}

def all_learning_outcomes(include_simulated: bool = False) -> list[dict]:
    with engine.begin() as conn:
        stmt = select(learning_outcomes).order_by(learning_outcomes.c.id.asc())
        if not include_simulated:
            stmt = stmt.where(learning_outcomes.c.is_simulated == 0)
        rows = conn.execute(stmt).mappings().all()
        return [dict(r) for r in rows]



def get_attribution_dataset(
    attribution_window_days: int = 14,
    now: datetime.datetime | None = None,
    include_simulated: bool = False,
) -> dict:
    """Build the authoritative, exact-link dataset; only in-window outcomes count."""
    if attribution_window_days < 1:
        raise ValueError("attribution_window_days must be >= 1")
    ref_now = _ensure_utc(now) or datetime.datetime.now(datetime.timezone.utc)
    window = datetime.timedelta(days=attribution_window_days)
    with engine.begin() as conn:
        decisions = [dict(r) for r in conn.execute(select(learning_decisions)).mappings().all()]
        outreach_rows = {r["id"]: dict(r) for r in conn.execute(select(outreach)).mappings().all()}
        outcomes = [dict(r) for r in conn.execute(select(learning_outcomes)).mappings().all()]
        legacy_events = [dict(r) for r in conn.execute(select(events)).mappings().all()]

    def is_simulated_decision(d):
        ctx = d.get("context_json")
        return bool(isinstance(ctx, dict) and (ctx.get("is_simulated_exposure") or ctx.get("simulated")))

    decisions_by_id = {d["id"]: d for d in decisions}
    valid_outcomes_by_decision: dict[int, list[dict]] = {}
    valid_outcome_ids: set[int] = set()
    validated_linked_outreach_ids: set[int] = set()
    invalid_outcome_count = 0
    for o in outcomes:
        dec = decisions_by_id.get(o["decision_id"])
        out = outreach_rows.get(o["outreach_id"])
        if not dec or not out or dec.get("outreach_id") != o["outreach_id"] or dec.get("lead_id") != o["lead_id"] or out.get("lead_id") != o["lead_id"]:
            invalid_outcome_count += 1
            continue
        sent_at = _ensure_utc(out.get("sent_at"))
        occurred_at = _ensure_utc(o.get("occurred_at"))
        if not sent_at or not occurred_at or occurred_at < sent_at or occurred_at > sent_at + window:
            invalid_outcome_count += 1
            continue
        validated_linked_outreach_ids.add(o["outreach_id"])
        decision_simulated = is_simulated_decision(dec)
        # Keep synthetic and real cohorts separate; never blend their outcomes.
        if include_simulated:
            if not decision_simulated or not bool(o.get("is_simulated")):
                continue
        elif decision_simulated or bool(o.get("is_simulated")):
            continue
        valid_outcomes_by_decision.setdefault(dec["id"], []).append(o)
        valid_outcome_ids.add(o["id"])

    # General events with no trustworthy learning-outcome link remain excluded from learning.
    attributed_outreach_ids = validated_linked_outreach_ids
    unattributed_events_count = 0
    for e in legacy_events:
        if e.get("event_type") not in SUPPORTED_OUTCOMES:
            continue
        meta = e.get("event_metadata") or ""
        is_attr = False
        match = re.search(r"(?:^|[;\s])outreach_id=(\d+)(?:$|[;\s])", meta)
        if match:
            oid = int(match.group(1))
            out = outreach_rows.get(oid)
            is_attr = bool(out and out.get("lead_id") == e.get("lead_id") and oid in attributed_outreach_ids)
        if not is_attr:
            unattributed_events_count += 1

    def new_bucket():
        return {"eligible_sent": 0, "mature_sent": 0, "immature_sent": 0,
                "replied": 0, "meetings": 0, "won": 0, "lost": 0,
                "bounced": 0, "unsubscribed": 0, "opened": 0}
    variant_stats = {"A": new_bucket(), "B": new_bucket()}
    segment_stats: dict[str, dict[str, dict]] = {}
    records = []
    sent_exposures = mature_exposures = immature_exposures = 0
    excluded_simulated_exposures = excluded_real_exposures = 0

    for d in decisions:
        action = d.get("action")
        if action not in ("A", "B"):
            continue
        ctx = d.get("context_json")
        simulated_decision = is_simulated_decision(d)
        seg = d.get("segment_key") or "all"
        oid = d.get("outreach_id")
        out = outreach_rows.get(oid) if oid else None
        sent_at = _ensure_utc(out.get("sent_at")) if out else None
        eligible = bool(out and out.get("channel") == "email" and out.get("sequence_step") == 0
                        and out.get("status") in ("sent", "replied", "bounced") and sent_at)
        if not eligible:
            continue
        if simulated_decision != bool(include_simulated):
            if simulated_decision:
                excluded_simulated_exposures += 1
            else:
                excluded_real_exposures += 1
            continue
        segment_stats.setdefault(seg, {"A": new_bucket(), "B": new_bucket()})
        sent_exposures += 1
        bucket, segment_bucket = variant_stats[action], segment_stats[seg][action]
        bucket["eligible_sent"] += 1
        segment_bucket["eligible_sent"] += 1
        is_mature = bool(sent_at and sent_at + window <= ref_now)
        if is_mature:
            mature_exposures += 1
            bucket["mature_sent"] += 1
            segment_bucket["mature_sent"] += 1
        else:
            immature_exposures += 1
            bucket["immature_sent"] += 1
            segment_bucket["immature_sent"] += 1

        dec_outcomes = valid_outcomes_by_decision.get(d["id"], [])
        flags = {
            "replied": any(o["event_type"] == "email_replied" for o in dec_outcomes),
            "meeting_booked": any(o["event_type"] == "meeting_booked" for o in dec_outcomes),
            "won": any(o["event_type"] == "won" for o in dec_outcomes),
            "lost": any(o["event_type"] == "lost" for o in dec_outcomes),
            "bounced": any(o["event_type"] == "bounced" for o in dec_outcomes),
            "unsubscribe": any(o["event_type"] == "unsubscribe" for o in dec_outcomes),
            "opened": any(o["event_type"] == "email_opened" for o in dec_outcomes),
        }
        records.append({
            "decision_id": d["id"], "decision_key": d["decision_key"], "lead_id": d["lead_id"],
            "outreach_id": oid, "action": action, "action_value": d.get("action_value"),
            "segment_key": seg, "policy_name": d.get("policy_name"),
            "policy_version": d.get("policy_version"),
            "assignment_probability": d.get("assignment_probability"),
            "sent_at": sent_at.isoformat() if sent_at else None, "is_mature": is_mature,
            "replied": int(flags["replied"]), "meeting_booked": int(flags["meeting_booked"]),
            "won": int(flags["won"]), "lost": int(flags["lost"]),
            "bounced": int(flags["bounced"]), "unsubscribe": int(flags["unsubscribe"]),
            "opened": int(flags["opened"]), "is_simulated": simulated_decision,
            "propensity_valid": not (isinstance(ctx, dict) and ctx.get("propensity_valid") is False),
        })
        if is_mature:
            event_map = {"replied": "replied", "meeting_booked": "meetings", "won": "won",
                         "lost": "lost", "bounced": "bounced", "unsubscribe": "unsubscribed", "opened": "opened"}
            for flag, bucket_key in event_map.items():
                if flags[flag]:
                    bucket[bucket_key] += 1
                    segment_bucket[bucket_key] += 1

    warnings = []
    if unattributed_events_count:
        warnings.append(f"{unattributed_events_count} legacy events lack verified outreach attribution and were excluded.")
    if immature_exposures:
        warnings.append(f"{immature_exposures} exposures are inside the {attribution_window_days}-day maturity window and were excluded from mature metrics.")
    if invalid_outcome_count:
        warnings.append(f"{invalid_outcome_count} outcomes had invalid links or timestamps outside the attribution window and were excluded.")
    if excluded_simulated_exposures:
        warnings.append(f"{excluded_simulated_exposures} simulated exposures were excluded from real-data metrics.")
    if excluded_real_exposures:
        warnings.append(f"{excluded_real_exposures} real exposures were excluded from the synthetic-only cohort.")
    selected_decision_count = sum(1 for d in decisions if is_simulated_decision(d) == bool(include_simulated))
    return {
        "attribution_window_days": attribution_window_days, "as_of_time": ref_now.isoformat(),
        "total_decisions": selected_decision_count, "sent_exposures": sent_exposures,
        "mature_exposures": mature_exposures, "immature_exposures": immature_exposures,
        "excluded_simulated_exposures": excluded_simulated_exposures,
        "excluded_real_exposures": excluded_real_exposures,
        "attributed_outcomes_count": len(valid_outcome_ids),
        "unattributed_events_count": unattributed_events_count,
        "excluded_outcomes_invalid_or_outside_window": invalid_outcome_count,
        "variant_stats": variant_stats, "segment_stats": segment_stats,
        "data_quality_warnings": warnings, "records": records,
    }

