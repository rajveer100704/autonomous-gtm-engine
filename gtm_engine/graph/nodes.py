"""
LangGraph Graph Nodes — each node is a pure function: state_in → partial state_out.

Node responsibilities:
  planner_node      → sets campaign plan
  apollo_node       → fetches leads
  research_node     → researches current lead
  intelligence_node → runs company intelligence sub-agents
  pain_point_node   → detects business challenges
  content_node      → generates email + LinkedIn copy + quality review
  strategy_node     → decides outreach sequence
  queue_node        → enqueues execution tasks
  memory_node       → updates lead memory after execution
  response_monitor  → checks for replies (wired to scheduler wakeups)
"""
from __future__ import annotations

import datetime
import logging

log = logging.getLogger("gtm.graph.nodes")


# ── Planner ───────────────────────────────────────────────────────────────
def planner_node(state: dict) -> dict:
    from gtm_engine.agents.planner_agent import plan_campaign
    plan = plan_campaign(
        objective=state.get("objective", "Book Demo"),
        max_leads=state.get("max_leads", 10),
        focus=state.get("plan", {}).get("focus", ""),
    )
    log.info("planner_node: plan ready (objective=%s)", plan["objective"])
    return {"plan": plan, "results": [], "errors": [], "queue_ids": [], "execution_results": []}


# ── Apollo Lead Fetching ──────────────────────────────────────────────────
def apollo_node(state: dict) -> dict:
    from gtm_engine.clients import apollo_client
    from gtm_engine.crm import db

    db.init_db()
    max_leads = state.get("max_leads", 10)
    raw_leads = apollo_client.search_leads(per_page=max_leads)
    log.info("apollo_node: fetched %d leads", len(raw_leads))
    return {"raw_leads": raw_leads, "current_lead_index": 0}


# ── Enrichment ────────────────────────────────────────────────────────────
def enrichment_node(state: dict) -> dict:
    from gtm_engine.agents.enrichment_agent import enrich
    from gtm_engine.services.crm_service import crm
    from gtm_engine import tracing

    raw_leads = state.get("raw_leads", [])
    idx = state.get("current_lead_index", 0)
    if idx >= len(raw_leads):
        return {"campaign_complete": True}

    raw = raw_leads[idx]
    enriched = enrich(raw)
    lead_id = crm.insert_lead(enriched)
    enriched["id"] = lead_id

    # Load memory for this lead
    from gtm_engine.memory.lead_memory import lead_memory
    memory_ctx = lead_memory.build_context_prompt(lead_id)

    log.info("enrichment_node: lead %d — %s @ %s", lead_id, enriched["first_name"], enriched["company"])
    return {
        "current_lead": enriched,
        "memory_context": memory_ctx,
        "research": {},
        "pain_points": [],
        "strategy": {},
    }


# ── Research ──────────────────────────────────────────────────────────────
def research_node(state: dict) -> dict:
    from gtm_engine.agents import research_agent
    from gtm_engine.services.crm_service import crm
    from gtm_engine import tracing

    lead = state["current_lead"]
    lead_id = lead["id"]
    trace_id = tracing.new_trace_id(lead_id)

    with tracing.span(trace_id, "research", lead_id=lead_id):
        research_out = research_agent.research(lead, lead_id=lead_id)

    crm.save_research(
        lead_id,
        research_out.get("summary", ""),
        "; ".join(research_out.get("signals", [])),
        "; ".join(f"{s['title']}|{s['url']}" for s in research_out.get("sources", [])),
        research_out.get("confidence", "low"),
    )
    log.info("research_node: lead %d confidence=%s", lead_id, research_out.get("confidence"))
    return {"research": research_out}


# ── Company Intelligence ──────────────────────────────────────────────────
async def intelligence_node(state: dict) -> dict:
    from gtm_engine.agents.company_intelligence import gather_company_intelligence_async

    lead = state["current_lead"]
    intel = await gather_company_intelligence_async(
        domain=lead.get("domain", ""),
        company_name=lead.get("company", ""),
        lead_id=lead.get("id"),
    )
    log.info("intelligence_node: lead %d — %d signals (parallelized)", lead["id"], len(intel.get("merged_signals", [])))
    return {"company_intelligence": intel}


# ── Pain Point Detection ──────────────────────────────────────────────────
def pain_point_node(state: dict) -> dict:
    from gtm_engine.agents import pain_point_agent
    from gtm_engine.services.crm_service import crm
    from gtm_engine import tracing

    lead = state["current_lead"]
    lead_id = lead["id"]
    trace_id = tracing.new_trace_id(lead_id)
    research = state.get("research", {})

    # Enrich research with intelligence signals before pain detection
    intel = state.get("company_intelligence", {})
    if intel.get("merged_signals"):
        research = {**research, "signals": research.get("signals", []) + intel["merged_signals"]}

    with tracing.span(trace_id, "pain_point", lead_id=lead_id):
        pain_out = pain_point_agent.detect(research, lead_id=lead_id)

    pain_points = pain_out.get("pain_points", [])
    crm.save_pain_points(lead_id, "; ".join(pain_points), pain_out.get("confidence", ""))
    log.info("pain_point_node: lead %d — %d pain points", lead_id, len(pain_points))
    return {"pain_points": pain_points}


# ── Content Generation ────────────────────────────────────────────────────
def content_node(state: dict) -> dict:
    from gtm_engine.agents import email_agent, linkedin_agent, ab_test, reflection_agent, evaluation_agent
    from gtm_engine.services.crm_service import crm
    from gtm_engine import tracing

    lead = state["current_lead"]
    lead_id = lead["id"]
    trace_id = tracing.new_trace_id(lead_id)
    pain_points = state.get("pain_points", [])
    top_pain = pain_points[0] if pain_points else "general inefficiency"
    memory_context = state.get("memory_context", "")
    variant = ab_test.assign_variant()

    with tracing.span(trace_id, "email", lead_id=lead_id):
        email_copy = email_agent.generate(
            lead, pain_points, lead_id=lead_id,
            style=email_agent.VARIANT_STYLES[variant],
            feedback=memory_context,
        )

    # 1. First reflection rewrite check
    with tracing.span(trace_id, "reflect", lead_id=lead_id):
        review = reflection_agent.judge(email_copy, top_pain, lead_id=lead_id)

    if not review["approved"]:
        log.info("content_node: reflection review rejected, rewriting (lead=%d)", lead_id)
        with tracing.span(trace_id, "email_rewrite", lead_id=lead_id):
            email_copy = email_agent.generate(
                lead, pain_points, lead_id=lead_id,
                style=email_agent.VARIANT_STYLES[variant],
                feedback=review["feedback"],
            )

    # 2. V3 Two-Stage Evaluation Agent Check (Gating execution)
    eval_res = evaluation_agent.evaluate(email_copy, lead, pain_points)
    
    if not eval_res["approved"]:
        # If it still fails, flag it and fallback to a default safe draft or raise
        log.warning("content_node: V3 evaluation rejected lead=%d, drafting basic fallback", lead_id)
        email_copy["body"] = (
            f"Hi {lead.get('first_name', 'there')},\n\n"
            f"I noticed {lead.get('company', 'your company')} might be facing challenges with document review cycles. "
            f"I'd love to connect to see if we can help simplify this.\n\n"
            f"Best,\n{settings.sender_name}"
        )
        email_copy["subject"] = f"Quick question re: document processes at {lead.get('company', 'your company')}"

    email_id = crm.save_outreach(
        lead_id=lead_id, channel="email", step=0,
        subject=email_copy["subject"], body=email_copy["body"],
        variant=variant, status="drafted",
    )
    email_copy["outreach_id"] = email_id
    email_copy["variant"] = variant
    email_copy["evaluation"] = eval_res

    with tracing.span(trace_id, "linkedin", lead_id=lead_id):
        linkedin_copy = linkedin_agent.generate(lead, pain_points, lead_id=lead_id)

    linkedin_id = crm.save_outreach(
        lead_id=lead_id, channel="linkedin", step=0,
        subject=None, body=linkedin_copy["message"], status="drafted",
    )
    linkedin_copy["outreach_id"] = linkedin_id

    log.info("content_node: lead %d — email+linkedin generated and evaluated", lead_id)
    return {"email_copy": email_copy, "linkedin_copy": linkedin_copy}



# ── Strategy ──────────────────────────────────────────────────────────────
def strategy_node(state: dict) -> dict:
    from gtm_engine.agents.strategy_agent import decide_outreach_strategy
    from gtm_engine.memory.lead_memory import lead_memory

    lead = state["current_lead"]
    memory = lead_memory.load(lead["id"])
    strategy = decide_outreach_strategy(
        lead=lead,
        research=state.get("research", {}),
        pain_points=state.get("pain_points", []),
        memory=memory,
        lead_id=lead["id"],
    )
    log.info(
        "strategy_node: lead %d → objective=%s, skip=%s",
        lead["id"], strategy.get("objective"), strategy.get("skip")
    )
    return {"strategy": strategy}


# ── Execution Queue ───────────────────────────────────────────────────────
def queue_node(state: dict) -> dict:
    from gtm_engine.task_queue.execution_queue import execution_queue
    from gtm_engine.agents.followup_agent import schedule_followups_for_lead
    from gtm_engine.services.crm_service import crm

    lead = state["current_lead"]
    lead_id = lead["id"]
    strategy = state.get("strategy", {})
    email_copy = state.get("email_copy", {})
    linkedin_copy = state.get("linkedin_copy", {})

    queue_ids = []

    if strategy.get("skip"):
        log.info("queue_node: lead %d skipped (%s)", lead_id, strategy.get("skip_reason"))
        crm.update_lead_status(lead_id, "disqualified")
        return {"queue_ids": [], "campaign_complete": False}

    for step in strategy.get("recommended_sequence", []):
        channel = step.get("channel")
        delay = step.get("delay_days", 0)

        if channel == "email" and email_copy:
            task = {
                "type": "email",
                "to": lead.get("email", ""),
                "subject": email_copy.get("subject", ""),
                "body": email_copy.get("body", ""),
                "outreach_id": email_copy.get("outreach_id"),
                "delay_days": delay,
            }
            ikey = execution_queue.make_key(
                lead_id=lead_id,
                channel="email",
                step=delay,
                template_version=email_copy.get("variant", "default"),
            )
            qid = execution_queue.enqueue(lead_id, task, priority=1, idempotency_key=ikey)
            queue_ids.append(qid)

        elif channel == "linkedin" and linkedin_copy and lead.get("linkedin_url"):
            task = {
                "type": "linkedin",
                "profile_url": lead["linkedin_url"],
                "message": linkedin_copy.get("message", ""),
                "outreach_id": linkedin_copy.get("outreach_id"),
                "delay_days": delay,
            }
            ikey = execution_queue.make_key(
                lead_id=lead_id,
                channel="linkedin",
                step=delay,
                template_version="default",
            )
            qid = execution_queue.enqueue(lead_id, task, priority=2, idempotency_key=ikey)
            queue_ids.append(qid)

        elif channel == "followup":
            schedule_followups_for_lead({"id": lead_id}, lead)

    crm.update_lead_status(lead_id, "sequenced")
    log.info("queue_node: lead %d — %d tasks queued", lead_id, len(queue_ids))
    return {"queue_ids": queue_ids}



# ── Memory Update ─────────────────────────────────────────────────────────
def memory_update_node(state: dict) -> dict:
    from gtm_engine.memory.lead_memory import lead_memory

    lead = state["current_lead"]
    lead_id = lead["id"]
    email_copy = state.get("email_copy", {})

    if email_copy.get("subject"):
        lead_memory.update(
            lead_id,
            past_emails=[email_copy.get("subject", "")],
        )

    log.debug("memory_update_node: lead %d memory updated", lead_id)
    return {}


# ── Advance Lead Counter ──────────────────────────────────────────────────
def advance_node(state: dict) -> dict:
    """Move to the next lead or mark campaign complete."""
    idx = state.get("current_lead_index", 0) + 1
    raw_leads = state.get("raw_leads", [])
    lead = state.get("current_lead", {})
    email_copy = state.get("email_copy", {})
    pain_points = state.get("pain_points", [])
    strategy = state.get("strategy", {})

    result = {
        "lead_id": lead.get("id"),
        "name": lead.get("full_name", ""),
        "company": lead.get("company", ""),
        "pain_points": pain_points,
        "strategy_objective": strategy.get("objective", ""),
        "email_subject": email_copy.get("subject", ""),
    }

    is_complete = idx >= len(raw_leads)
    return {
        "current_lead_index": idx,
        "results": [result],
        "campaign_complete": is_complete,
    }


# ── Routing ───────────────────────────────────────────────────────────────
def should_continue(state: dict) -> str:
    """Route: process next lead or end campaign."""
    if state.get("campaign_complete"):
        return "END"
    raw_leads = state.get("raw_leads", [])
    idx = state.get("current_lead_index", 0)
    if idx >= len(raw_leads):
        return "END"
    return "enrich"
