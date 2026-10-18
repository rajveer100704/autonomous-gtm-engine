"""
FastAPI service — the deployable front door to the GTM engine.
POST /run-campaign   -> pulls leads, runs full pipeline, returns sequenced leads
GET  /leads          -> CRM lead list
GET  /outreach       -> CRM outreach list (emails/LinkedIn drafted + scheduled)
POST /run-followups  -> fires any due follow-ups (call this from a cron trigger)
"""
from contextlib import asynccontextmanager
import datetime
import hmac
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request, Header
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
    event_type: str
    metadata: str = ""
    reply_text: str = ""
    outreach_id: int | None = None
    decision_id: int | None = None
    event_key: str | None = None
    occurred_at: datetime.datetime | None = None
    source: str = "provider_webhook"
    is_simulated: bool = False


class ProviderTestSendRequest(BaseModel):
    outreach_id: int
    decision_id: int


def _dry_run_sender(outreach_row: dict) -> dict:
    """
    Placeholder transport: logs instead of actually sending. Swap for a real
    SendGrid/Gmail API call — see README 'Wiring real sends'. A real ESP
    integration would call POST /events/track (email_opened/email_replied)
    from its webhook once this is wired up for real.
    """
    print(f"[DRY RUN SEND] outreach_id={outreach_row['id']} -> {outreach_row.get('subject')}")
    return {"success": True, "simulated": True}

_dry_run_sender.is_simulated = True


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
def track_event(req: TrackEventRequest, request: Request = None):
    """Record an internal event; production calls require a shared internal token.

    Public provider callbacks must use /webhooks/sendgrid/events, which verifies
    the provider's signature over the original raw body.
    """
    from gtm_engine.config import settings
    if not settings.mock_mode:
        expected = settings.internal_event_token
        supplied = request.headers.get("X-GTM-Event-Token", "") if request is not None else ""
        if not expected:
            raise HTTPException(status_code=503, detail="Internal event ingestion is disabled: token not configured")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="Forbidden")
    lead = db.get_lead(req.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    learning_status = "unattributed_not_used_for_learning"
    learning_result = None
    resolved_outreach_id = req.outreach_id
    resolved_decision_id = req.decision_id
    should_attribute = (req.outreach_id is not None or req.decision_id is not None)

    if should_attribute:
        if resolved_decision_id is not None:
            decision = db.get_decision(resolved_decision_id)
            if not decision:
                raise HTTPException(status_code=404, detail="Learning decision not found")
            if decision["lead_id"] != req.lead_id:
                raise HTTPException(status_code=400, detail="Decision does not belong to supplied lead")
            if resolved_outreach_id is None:
                resolved_outreach_id = decision.get("outreach_id")
            elif decision.get("outreach_id") != resolved_outreach_id:
                raise HTTPException(status_code=400, detail="Decision does not point to supplied outreach")
        if resolved_outreach_id is None or resolved_decision_id is None:
            if resolved_outreach_id is not None:
                decision = db.get_decision_for_outreach(resolved_outreach_id)
                if decision and decision["lead_id"] == req.lead_id:
                    resolved_decision_id = decision["id"]
            if resolved_outreach_id is None or resolved_decision_id is None:
                raise HTTPException(status_code=400, detail="Explicit outreach_id and decision_id are required for learning attribution")
        out = next((o for o in db.all_outreach() if o["id"] == resolved_outreach_id), None)
        dec = db.get_decision(resolved_decision_id)
        if not out or not dec:
            raise HTTPException(status_code=404, detail="Outreach or decision not found")
        if out["lead_id"] != req.lead_id or dec["lead_id"] != req.lead_id or dec["outreach_id"] != resolved_outreach_id:
            raise HTTPException(status_code=400, detail="Lead, outreach, and decision identifiers do not match")
        if req.event_type in db.SUPPORTED_OUTCOMES:
            event_key = req.event_key or f"api:{resolved_decision_id}:{req.event_type}"
            try:
                learning_result = db.record_learning_outcome(
                    lead_id=req.lead_id, outreach_id=resolved_outreach_id, decision_id=resolved_decision_id,
                    event_type=req.event_type, event_key=event_key, occurred_at=req.occurred_at,
                    source=req.source, is_simulated=(req.is_simulated or req.source in {"simulator", "test_fixture"}),
                    metadata={"source": req.source} if req.source else {},
                )
                learning_status = learning_result["status"]
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            learning_status = "event_type_not_used_for_learning"

    # Preserve the legacy general event stream. Add an explicit link in metadata where trusted.
    meta = req.metadata or ""
    if resolved_outreach_id is not None:
        suffix = f"outreach_id={resolved_outreach_id}"
        if suffix not in meta:
            meta = f"{meta};{suffix}" if meta else suffix
    db.record_event(req.lead_id, req.event_type, meta)

    adaptive_result = None
    if req.event_type == "email_replied" and req.reply_text:
        adaptive_result = followup_agent.handle_reply(req.lead_id, req.reply_text)

    return {
        "status": "recorded", "lead_id": req.lead_id, "event_type": req.event_type,
        "attribution_status": learning_status, "learning_outcome": learning_result,
        "adaptive_followup": adaptive_result,
    }


@app.post("/webhooks/sendgrid/events")
async def sendgrid_events_webhook(request: Request):
    """Receive SendGrid events only after verifying the exact raw body signature."""
    from gtm_engine.providers.sendgrid_webhooks import (
        SIGNATURE_HEADER, TIMESTAMP_HEADER, process_events, verify_signature,
    )
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    raw_body = await request.body()
    if len(raw_body) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    if not verify_signature(
        raw_body, request.headers.get(SIGNATURE_HEADER), request.headers.get(TIMESTAMP_HEADER)
    ):
        raise HTTPException(status_code=403, detail="Invalid or missing SendGrid webhook signature")
    import json
    try:
        events_payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(events_payload, list) or any(not isinstance(item, dict) for item in events_payload):
        raise HTTPException(status_code=400, detail="Expected a JSON array of event objects")
    try:
        summary = process_events(events_payload)
    except Exception as exc:
        # Avoid returning internal exception details to the provider or caller.
        raise HTTPException(status_code=500, detail="Transient webhook processing failure") from exc
    return {"status": "accepted", **summary}


@app.post("/webhooks/sendgrid/inbound")
async def sendgrid_inbound_reply_webhook(request: Request):
    """Receive actual email replies through a signature-secured Inbound Parse hook."""
    from gtm_engine.providers.sendgrid_webhooks import (
        SIGNATURE_HEADER, TIMESTAMP_HEADER, verify_signature,
    )
    from gtm_engine.providers.sendgrid_inbound import process_inbound_reply
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Inbound message too large")
    raw_body = await request.body()
    if len(raw_body) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Inbound message too large")
    from gtm_engine.config import settings
    if not verify_signature(
        raw_body, request.headers.get(SIGNATURE_HEADER), request.headers.get(TIMESTAMP_HEADER),
        public_key_override=settings.sendgrid_inbound_webhook_public_key,
    ):
        raise HTTPException(status_code=403, detail="Invalid or missing SendGrid inbound signature")
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise HTTPException(status_code=400, detail="Expected multipart/form-data")
    try:
        result = process_inbound_reply(raw_body, content_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Transient inbound reply processing failure") from exc
    return {"status": "accepted", **result}


@app.post("/provider/send-test")
def send_provider_test_email(
    req: ProviderTestSendRequest, authorization: str | None = Header(default=None),
):
    """One-message staging smoke test; not a campaign endpoint.

    Requires GTM_MOCK_MODE=false, ENABLE_PROVIDER_SENDS=true, an auth token, and
    an exact recipient allowlist. Each attempt transitions the outreach to
    'sending' before the network call to prevent accidental duplicate sends.
    """
    from gtm_engine.config import settings
    if settings.mock_mode or not settings.enable_provider_sends:
        raise HTTPException(status_code=403, detail="Provider sending is disabled")
    if not settings.provider_send_auth_token:
        raise HTTPException(status_code=503, detail="Provider send token is not configured")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, settings.provider_send_auth_token):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not settings.provider_test_allowed_recipients:
        raise HTTPException(status_code=503, detail="No provider test recipients are allowlisted")
    if not settings.sendgrid_reply_to_domain or not settings.sendgrid_reply_token_secret:
        raise HTTPException(status_code=503, detail="Signed SendGrid reply routing is not configured")
    if not settings.sendgrid_api_key or not settings.sendgrid_from_email:
        raise HTTPException(status_code=503, detail="SendGrid API key or verified sender address is not configured")
    decision = db.get_decision(req.decision_id)
    outreach_row = next((row for row in db.all_outreach() if row["id"] == req.outreach_id), None)
    if not decision or not outreach_row:
        raise HTTPException(status_code=404, detail="Outreach or decision not found")
    if (decision.get("outreach_id") != req.outreach_id
            or decision.get("lead_id") != outreach_row.get("lead_id")
            or decision.get("decision_type") != "email_style"
            or outreach_row.get("channel") != "email"
            or outreach_row.get("sequence_step") != 0):
        raise HTTPException(status_code=400, detail="Decision must match the exact first-touch email outreach")
    lead = db.get_lead(outreach_row["lead_id"])
    recipient = (lead or {}).get("email", "")
    if not recipient or recipient.lower() not in settings.provider_test_allowed_recipients:
        raise HTTPException(status_code=403, detail="Outreach recipient is not on the provider test allowlist")
    if outreach_row.get("status") not in {"drafted", "scheduled"} or outreach_row.get("sent_at") is not None:
        raise HTTPException(status_code=409, detail="Outreach is not an unsent draft/scheduled item")

    # Claim before calling the provider. If the process crashes during the API call,
    # the row remains 'sending' and must be reconciled manually before retrying.
    db.update_outreach_status(req.outreach_id, "sending")
    from gtm_engine.executors.sendgrid_executor import send_email
    from gtm_engine.providers.sendgrid_webhooks import make_reply_to_address
    result = send_email(
        recipient, outreach_row.get("subject") or "", outreach_row.get("body") or "",
        custom_args={"lead_id": outreach_row["lead_id"], "outreach_id": req.outreach_id,
                     "decision_id": req.decision_id, "campaign_run_id": decision.get("campaign_run_id")},
        reply_to=make_reply_to_address(outreach_row["lead_id"], req.outreach_id, req.decision_id),
    )
    if not result.get("success") or result.get("mock"):
        db.update_outreach_status(req.outreach_id, "send_failed")
        raise HTTPException(status_code=502, detail={"error": "Provider did not accept the staging send",
                                                      "error_code": result.get("error_code", "unexpected_mock_result")})
    sent_at = datetime.datetime.now(datetime.timezone.utc)
    db.update_outreach_status(req.outreach_id, "sent", sent_at=sent_at)
    db.record_event(outreach_row["lead_id"], "email_sent", f"provider=sendgrid;outreach_id={req.outreach_id};decision_id={req.decision_id}")
    return {"status": "provider_accepted", "provider": "sendgrid",
            "outreach_id": req.outreach_id, "decision_id": req.decision_id,
            "message_id": result.get("message_id")}


@app.get("/health/ready")
def readiness():
    """Readiness probe that verifies database connectivity, not merely process liveness."""
    try:
        with db.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return {"status": "ready", "database": "reachable",
                "database_url_kind": "postgres" if "postgres" in str(db.engine.url) else "sqlite"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc


@app.get("/analytics/replies")
def analytics_replies():
    return db.reply_classification_counts()


@app.get("/analytics/ab-test")
def analytics_ab_test():
    return {
        "performance": db.variant_performance(),
        "active_winner": db.get_validated_active_winner(),
        "legacy_historical_winner": db.get_active_winner(),
    }


@app.post("/analytics/ab-test/evaluate")
def evaluate_ab_test():
    """
    Checks current variant performance and promotes a winner if one variant
    has enough samples and a clear enough lead — see agents/ab_test.py for
    thresholds. Safe to call repeatedly (idempotent no-op if no winner yet).
    """
    return ab_test.evaluate_and_promote()


@app.get("/analytics/outcome-feedback")
def analytics_outcome_feedback(attribution_window_days: int = Query(14, ge=1, le=365)):
    from gtm_engine.optimization.feedback import OutcomeFeedbackService
    return OutcomeFeedbackService.get_dataset_summary(attribution_window_days=attribution_window_days)


@app.get("/optimization/recommendation")
def optimization_recommendation(
    segment: str = "all",
    attribution_window_days: int = Query(14, ge=1, le=365),
    include_simulated: bool = False,
):
    from gtm_engine.optimization.feedback import OutcomeFeedbackService
    if include_simulated:
        from gtm_engine.config import settings
        if not settings.mock_mode:
            raise HTTPException(status_code=400, detail="Simulated data is only available in mock mode")
    return OutcomeFeedbackService.get_recommendation(
        segment_key=segment, attribution_window_days=attribution_window_days,
        include_simulated=include_simulated,
    )


@app.post("/optimization/evaluate")
def optimization_evaluate(attribution_window_days: int = Query(14, ge=1, le=365)):
    """Explicit operator action; evaluates real attributed data and never sends outreach."""
    return ab_test.evaluate_and_promote(attribution_window_days=attribution_window_days)


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


