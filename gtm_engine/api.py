"""
FastAPI service — the deployable front door to the GTM engine.
POST /run-campaign   -> pulls leads, runs full pipeline, returns sequenced leads
GET  /leads          -> CRM lead list
GET  /outreach       -> CRM outreach list (emails/LinkedIn drafted + scheduled)
POST /run-followups  -> fires any due follow-ups (call this from a cron trigger)
"""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from gtm_engine import orchestrator
from gtm_engine.agents import followup_agent, ab_test
from gtm_engine.crm import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


from fastapi import APIRouter
from fastapi.responses import JSONResponse

app = FastAPI(title="Autonomous GTM Engine", version="1.0.0", lifespan=lifespan)
router = APIRouter()

class AppProxy:
    def __init__(self, real_app, rt):
        self._real_app = real_app
        self._router = rt
    def get(self, *args, **kwargs):
        return self._router.get(*args, **kwargs)
    def post(self, *args, **kwargs):
        return self._router.post(*args, **kwargs)
    def patch(self, *args, **kwargs):
        return self._router.patch(*args, **kwargs)
    def delete(self, *args, **kwargs):
        return self._router.delete(*args, **kwargs)
    def put(self, *args, **kwargs):
        return self._router.put(*args, **kwargs)
    def __getattr__(self, name):
        return getattr(self._real_app, name)

app = AppProxy(app, router)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_web_dir = Path(__file__).parent / "web"
if _web_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_web_dir), html=True), name="ui")


class RunCampaignRequest(BaseModel):
    max_leads: int = 10
    objective: str = "Book Demo"
    focus: str = ""
    use_graph: bool = False  # set True to use LangGraph orchestrator
    human_approval: bool | None = None  # None = use settings.human_approval_mode


class TrackEventRequest(BaseModel):
    lead_id: int
    event_type: str  # email_opened | email_replied | meeting_booked | won | lost
    metadata: str = ""
    reply_text: str = ""  # required for event_type == "email_replied" to trigger classification


def _dry_run_sender(outreach_row: dict) -> bool:
    """
    Placeholder transport: logs instead of actually sending. Swap for a real
    SendGrid/Gmail API call — see README 'Wiring real sends'. A real ESP
    integration would call POST /events/track (email_opened/email_replied)
    from its webhook once this is wired up for real.
    """
    print(f"[DRY RUN SEND] outreach_id={outreach_row['id']} -> {outreach_row['subject']}")
    return True


@app.post("/run-campaign")
def run_campaign(req: RunCampaignRequest):
    if req.use_graph:
        # LangGraph path (stateful, with checkpointing)
        from gtm_engine.graph.graph import run_campaign_graph
        from gtm_engine.config import settings
        ha = req.human_approval if req.human_approval is not None else settings.human_approval_mode
        result = run_campaign_graph(
            max_leads=req.max_leads,
            objective=req.objective,
            focus=req.focus,
            human_approval=ha,
        )
        return {"count": len(result["results"]), "leads": result["results"], "mode": "langgraph"}
    else:
        # Legacy linear path (keeps all existing tests passing)
        results = orchestrator.run_pipeline(max_leads=req.max_leads, sender_fn=_dry_run_sender)
        return {"count": len(results), "leads": results, "mode": "linear"}


@app.get("/leads")
def get_leads():
    return db.all_leads()


@app.get("/outreach")
def get_outreach():
    return db.all_outreach()


@app.post("/run-followups")
def run_followups():
    results = followup_agent.run_due_followups(_dry_run_sender)
    return {"processed": len(results), "results": results}


@app.post("/events/track")
def track_event(req: TrackEventRequest):
    """
    Webhook receiver. Point your ESP's open/reply webhook (or a manual CRM
    action for 'meeting_booked' / 'won') at this endpoint. If event_type is
    'email_replied' and reply_text is provided, the reply is classified and
    the lead's remaining follow-up sequence adapts automatically —
    see agents/followup_agent.handle_reply.
    """
    db.record_event(req.lead_id, req.event_type, req.metadata)

    adaptive_result = None
    if req.event_type == "email_replied" and req.reply_text:
        adaptive_result = followup_agent.handle_reply(req.lead_id, req.reply_text)

    return {
        "status": "recorded", "lead_id": req.lead_id, "event_type": req.event_type,
        "adaptive_followup": adaptive_result,
    }


@app.get("/analytics/replies")
def analytics_replies():
    return db.reply_classification_counts()


@app.get("/analytics/ab-test")
def analytics_ab_test():
    return {
        "performance": db.variant_performance(),
        "active_winner": db.get_active_winner(),
    }


@app.post("/analytics/ab-test/evaluate")
def evaluate_ab_test():
    """
    Checks current variant performance and promotes a winner if one variant
    has enough samples and a clear enough lead — see agents/ab_test.py for
    thresholds. Safe to call repeatedly (idempotent no-op if no winner yet).
    """
    return ab_test.evaluate_and_promote()


@app.get("/analytics/funnel")
def analytics_funnel():
    return db.funnel_counts()


@app.get("/analytics/costs")
def analytics_costs():
    summary = db.cost_summary()
    summary["avg_cost_per_lead_usd"] = db.cost_per_lead()
    return summary


@app.post("/demo/simulate-engagement")
def simulate_engagement():
    """
    Mock-mode only. Randomly advances some sent outreach through
    opened -> replied -> meeting -> won so the funnel/analytics dashboard
    has something to show without waiting on real email replies.
    """
    import random
    from gtm_engine.config import settings

    if not settings.mock_mode:
        return {"error": "simulate-engagement is only available in GTM_MOCK_MODE=true"}

    leads = db.all_leads()
    simulated = []
    for lead in leads:
        lead_id = lead["id"]
        if random.random() < 0.7:
            db.record_event(lead_id, "email_opened")
            simulated.append((lead_id, "email_opened"))
        if random.random() < 0.35:
            db.record_event(lead_id, "email_replied")
            simulated.append((lead_id, "email_replied"))
        if random.random() < 0.15:
            db.record_event(lead_id, "meeting_booked")
            simulated.append((lead_id, "meeting_booked"))
        if random.random() < 0.05:
            db.record_event(lead_id, "won")
            simulated.append((lead_id, "won"))
    return {"simulated_events": len(simulated)}


@app.get("/observability/latency")
def observability_latency():
    return db.latency_summary()


@app.get("/observability/trace/{lead_id}")
def observability_trace(lead_id: int):
    return db.trace_for_lead(lead_id)


@app.get("/companies")
def get_companies():
    return db.company_summary()


@app.get("/research")
def get_research():
    return db.all_research_cache()


@app.get("/leads/{lead_id}")
def get_lead_detail(lead_id: int):
    detail = db.lead_detail(lead_id)
    if not detail:
        return {"error": "lead not found"}
    return detail


@app.get("/settings")
def get_settings():
    from gtm_engine.config import settings as cfg
    return {
        "mock_mode": cfg.mock_mode,
        "sender_name": cfg.sender_name,
        "sender_company": cfg.sender_company,
        "sender_product_pitch": cfg.sender_product_pitch,
        "follow_up_delay_days": cfg.follow_up_delay_days,
        "max_follow_ups": cfg.max_follow_ups,
        "research_cache_ttl_hours": cfg.research_cache_ttl_hours,
        "icp_titles": cfg.icp_titles,
        "icp_industries": cfg.icp_industries,
        "database_url_kind": "postgres" if "postgres" in cfg.database_url else "sqlite",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


# ── Queue status ───────────────────────────────────────────────────────────
@app.get("/queue/status")
def queue_status():
    from gtm_engine.task_queue.execution_queue import execution_queue
    return execution_queue.stats()


@app.post("/queue/process")
def queue_process():
    """Drain the execution queue — sends pending emails and LinkedIn tasks."""
    from gtm_engine.task_queue.worker import Worker
    worker = Worker()
    results = worker.process_all()
    return {"processed": len(results), "results": results}


# ── Lead memory ────────────────────────────────────────────────────────────
@app.get("/memory/{lead_id}")
def get_lead_memory(lead_id: int):
    from gtm_engine.memory.lead_memory import lead_memory
    return lead_memory.load(lead_id)


@app.patch("/memory/{lead_id}")
def update_lead_memory(lead_id: int, body: dict):
    from gtm_engine.memory.lead_memory import lead_memory
    lead_memory.update(lead_id, **body)
    return {"status": "updated", "lead_id": lead_id}


# ── Strategy preview ───────────────────────────────────────────────────────
@app.post("/strategy/preview")
def preview_strategy(lead_id: int):
    """
    Run the strategy agent for an existing lead and return its recommended
    outreach sequence without executing anything.
    """
    detail = db.lead_detail(lead_id)
    if not detail:
        return {"error": "lead not found"}
    lead = detail["lead"]
    pain_pts = (detail.get("pain_points", {}).get("pain_points") or "").split("; ")
    research = detail.get("research") or {}
    from gtm_engine.agents.strategy_agent import decide_outreach_strategy
    strategy = decide_outreach_strategy(lead=lead, research=research, pain_points=pain_pts, lead_id=lead_id)
    return strategy


# ── LangGraph execution trace ─────────────────────────────────────────────
@app.get("/graph/state/{thread_id}")
def get_graph_state(thread_id: str):
    """Return the persisted LangGraph state for a campaign thread."""
    try:
        from gtm_engine.graph.graph import build_campaign_graph
        from gtm_engine.config import settings
        graph = build_campaign_graph(human_approval=settings.human_approval_mode)
        config = {"configurable": {"thread_id": thread_id}}
        state = graph.get_state(config)
        return {"values": state.values if state else {}, "thread_id": thread_id}
    except Exception as exc:
        return {"error": str(exc)}


# ── Campaign Analytics ────────────────────────────────────────────────────
@app.get("/analytics/campaign")
def get_campaign_analytics():
    return db.campaign_analytics()


# ── Dead Letter Queue (DLQ) ────────────────────────────────────────────────
@app.get("/dlq")
def get_dlq_entries(status: str | None = None):
    from gtm_engine.task_queue.dead_letter_queue import dead_letter_queue
    if status == "all":
        return dead_letter_queue.all_entries()
    return dead_letter_queue.pending()


@app.post("/dlq/{entry_id}/requeue")
def requeue_dlq_entry(entry_id: int):
    from gtm_engine.task_queue.dead_letter_queue import dead_letter_queue
    from gtm_engine.task_queue.execution_queue import execution_queue
    
    entry = dead_letter_queue.get(entry_id)
    if not entry:
        return {"error": "DLQ entry not found"}
        
    # Re-enqueue the original payload
    new_id = execution_queue.enqueue(
        lead_id=entry.get("lead_id"),
        task=entry["payload"],
        idempotency_key=entry.get("idempotency_key"),
        max_retries=3,  # Reset retries count
    )
    dead_letter_queue.mark_requeued(entry_id)
    return {"status": "requeued", "new_queue_id": new_id}


@app.post("/dlq/{entry_id}/resolve")
def resolve_dlq_entry(entry_id: int, note: str = "Resolved by human"):
    from gtm_engine.task_queue.dead_letter_queue import dead_letter_queue
    dead_letter_queue.resolve(entry_id, resolution_note=note)
    return {"status": "resolved"}


# ── Audit Log ─────────────────────────────────────────────────────────────
@app.get("/audit/log")
def get_audit_log(limit: int = 100, lead_id: int | None = None):
    from gtm_engine.audit.audit_log import audit_log
    return audit_log.recent(limit=limit, lead_id=lead_id)


@app.get("/audit/stats")
def get_audit_stats():
    from gtm_engine.audit.audit_log import audit_log
    return audit_log.stats()


# Additional endpoints for V4.1
@app.get("/health")
@app.get("/api/v1/health")
def health():
    return {"status": "healthy"}

@app.get("/live")
def live():
    return {"status": "live"}

@app.get("/ready")
def ready():
    from gtm_engine.crm.db import engine
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "unready", "error": str(e)})

@app.get("/metrics")
def metrics():
    try:
        from gtm_engine.task_queue.execution_queue import execution_queue
        from gtm_engine.task_queue.dead_letter_queue import dead_letter_queue
        from gtm_engine.observability.metrics import QUEUE_DEPTH_GAUGE, DLQ_COUNT_GAUGE
        
        QUEUE_DEPTH_GAUGE.set(execution_queue.pending_count())
        DLQ_COUNT_GAUGE.set(len(dead_letter_queue.pending()))
    except Exception:
        pass
        
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Unpack the real app for mounting
real_app = app._real_app
real_app.include_router(app._router, prefix="/api/v1")
real_app.include_router(app._router)

# Re-expose the real FastAPI app
app = real_app


