"""
Orchestrator: runs the full pipeline —
Target ICP -> Apollo -> Enrichment -> Research -> Pain Points ->
Email + LinkedIn copy -> CRM -> follow-ups scheduled.

This is the "one flagship script" that proves the whole system works
end to end. Each stage is a swappable module (see gtm_engine/agents/*
and gtm_engine/clients/*) — nothing here is hardcoded to a specific
vendor beyond the imports.
"""
import datetime
import logging

from gtm_engine.clients import apollo_client
from gtm_engine.agents import (
    enrichment_agent, research_agent, pain_point_agent, email_agent,
    linkedin_agent, followup_agent, ab_test, reflection_agent,
)
from gtm_engine import tracing
from gtm_engine.crm import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gtm_orchestrator")


def run_pipeline(max_leads: int = 10, sender_fn=None) -> list[dict]:
    """
    sender_fn(outreach_row: dict) -> bool, used to "send" the first-touch
    outreach right after drafting so the funnel has real sent/opened/
    replied data instead of everything sitting at 'drafted'. Defaults to a
    dry-run no-op that always succeeds.
    """
    if sender_fn is None:
        sender_fn = lambda row: True  # noqa: E731

    db.init_db()
    raw_leads = apollo_client.search_leads(per_page=max_leads)
    log.info("Pulled %d leads from Apollo", len(raw_leads))

    results = []
    for raw_lead in raw_leads:
        enriched = enrichment_agent.enrich(raw_lead)
        log.info("Enriched lead: %s @ %s", enriched["first_name"], enriched["company"])

        lead_id = db.insert_lead(enriched)
        trace_id = tracing.new_trace_id(lead_id)
        db.record_event(lead_id, "lead_sourced")

        with tracing.span(trace_id, "research", lead_id=lead_id):
            research_output = research_agent.research(enriched, lead_id=lead_id)
        db.save_research(
            lead_id,
            research_output.get("summary", ""),
            "; ".join(research_output.get("signals", [])),
            "; ".join(f"{s['title']}|{s['url']}" for s in research_output.get("sources", [])),
            research_output.get("confidence", ""),
        )

        with tracing.span(trace_id, "pain_point", lead_id=lead_id):
            pain_output = pain_point_agent.detect(research_output, lead_id=lead_id)
        pain_points = pain_output.get("pain_points", [])
        db.save_pain_points(lead_id, "; ".join(pain_points), pain_output.get("confidence", ""))

        email_variant = ab_test.assign_variant()
        top_pain = pain_points[0] if pain_points else "general inefficiency"
        with tracing.span(trace_id, "email", lead_id=lead_id):
            email_copy = email_agent.generate(
                enriched, pain_points, lead_id=lead_id,
                style=email_agent.VARIANT_STYLES[email_variant],
            )
        with tracing.span(trace_id, "reflect", lead_id=lead_id):
            review = reflection_agent.judge(email_copy, top_pain, lead_id=lead_id)
        if not review["approved"]:
            log.info("Reflection rejected draft (score=%s) for lead_id=%s — rewriting once",
                      review["score"], lead_id)
            with tracing.span(trace_id, "email_rewrite", lead_id=lead_id):
                email_copy = email_agent.generate(
                    enriched, pain_points, lead_id=lead_id,
                    style=email_agent.VARIANT_STYLES[email_variant],
                    feedback=review["feedback"],
                )

        email_outreach_id = db.save_outreach(
            lead_id=lead_id, channel="email", step=0,
            subject=email_copy["subject"], body=email_copy["body"],
            variant=email_variant,
            scheduled_for=datetime.datetime.now(datetime.timezone.utc), status="drafted",
        )
        if sender_fn({"id": email_outreach_id, "subject": email_copy["subject"]}):
            db.update_outreach_status(email_outreach_id, "sent",
                                       sent_at=datetime.datetime.now(datetime.timezone.utc))
            db.record_event(lead_id, "email_sent", metadata=f"outreach_id={email_outreach_id}")

        with tracing.span(trace_id, "linkedin", lead_id=lead_id):
            linkedin_copy = linkedin_agent.generate(enriched, pain_points, lead_id=lead_id)
        linkedin_outreach_id = db.save_outreach(
            lead_id=lead_id, channel="linkedin", step=0,
            subject=None, body=linkedin_copy["message"],
            scheduled_for=datetime.datetime.now(datetime.timezone.utc), status="drafted",
        )
        if sender_fn({"id": linkedin_outreach_id, "subject": "linkedin"}):
            db.update_outreach_status(linkedin_outreach_id, "sent",
                                       sent_at=datetime.datetime.now(datetime.timezone.utc))
            db.record_event(lead_id, "linkedin_sent", metadata=f"outreach_id={linkedin_outreach_id}")

        followup_agent.schedule_followups_for_lead({"id": lead_id}, enriched)
        db.update_lead_status(lead_id, "sequenced")

        results.append({
            "lead_id": lead_id,
            "name": enriched["full_name"],
            "company": enriched["company"],
            "pain_points": pain_points,
            "email_outreach_id": email_outreach_id,
            "email_variant": email_variant,
            "linkedin_outreach_id": linkedin_outreach_id,
            "email_subject": email_copy["subject"],
            "email_body": email_copy["body"],
            "linkedin_message": linkedin_copy["message"],
        })
        log.info("Completed sequence for lead_id=%s (%s)", lead_id, enriched["company"])

    return results


if __name__ == "__main__":
    output = run_pipeline(max_leads=3)
    for r in output:
        print("\n---")
        print(f"{r['name']} @ {r['company']}")
        print("Pain points:", r["pain_points"])
        print("Email subject:", r["email_subject"])
        print("Email body:\n", r["email_body"])
        print("LinkedIn message:\n", r["linkedin_message"])
