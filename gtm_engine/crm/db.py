"""
CRM data layer. Uses SQLAlchemy Core so the SAME code runs against:
  - Postgres in production  (DATABASE_URL=postgresql+psycopg2://user:pass@host/db)
  - SQLite for local dev/testing (default, no server required)

All agents talk to this module only — never raw SQL elsewhere.
"""
import datetime

from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Text, DateTime,
    ForeignKey, select, update, JSON
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


def init_db():
    metadata.create_all(engine)


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

