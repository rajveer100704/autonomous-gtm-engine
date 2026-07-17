import os
os.environ["GTM_MOCK_MODE"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///test_gtm_pipeline.db"

import pytest
from gtm_engine.orchestrator import run_pipeline
from gtm_engine.crm import db


@pytest.fixture(autouse=True)
def clean_db():
    from gtm_engine.crm import db as db_module
    from gtm_engine.task_queue.execution_queue import execution_queue
    from gtm_engine.task_queue.dead_letter_queue import init_dlq
    from gtm_engine.audit.audit_log import audit_log

    db_module.recreate_db_engine("sqlite:///test_gtm_pipeline.db")
    db_module.engine.dispose()
    for db_file in ["test_gtm_pipeline.db", "test_graph_state_pipeline.db"]:

        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except Exception:
                pass
    db_module.init_db()
    execution_queue._ensure_table()
    init_dlq()
    
    with db_module.engine.begin() as conn:
        for table in reversed(db_module.metadata.sorted_tables):
            try:
                conn.execute(table.delete())
            except Exception:
                pass
    yield
    db_module.engine.dispose()





def test_full_pipeline_runs_end_to_end():
    results = run_pipeline(max_leads=3)

    assert len(results) == 3
    for r in results:
        assert r["lead_id"] is not None
        assert r["pain_points"], "pain points should not be empty"
        assert r["email_subject"]
        assert r["email_body"]
        assert "{first_name}" not in r["email_body"], "placeholders must be resolved"
        assert "{company}" not in r["email_body"]
        assert r["linkedin_message"]
        assert "{first_name}" not in r["linkedin_message"]


def test_leads_persisted_in_crm():
    run_pipeline(max_leads=2)
    leads = db.all_leads()
    assert len(leads) == 2
    assert leads[0]["status"] == "sequenced"


def test_outreach_includes_scheduled_followups():
    run_pipeline(max_leads=1)
    outreach = db.all_outreach()
    # 1 email (step 0) + 1 linkedin (step 0) + N scheduled follow-ups
    steps = sorted(o["sequence_step"] for o in outreach)
    scheduled = [o for o in outreach if o["status"] == "scheduled"]
    assert 0 in steps
    assert len(scheduled) >= 1


def test_research_is_grounded_in_search_results():
    from gtm_engine.agents import research_agent

    db.init_db()
    lead = {"company": "Bramble Legal Tech", "domain": "bramblelegal.com",
            "raw": {"industry": "Legal Tech"}, "company_size_bucket": "small (20-75)"}
    result = research_agent.research(lead)

    assert result["summary"]
    assert isinstance(result["signals"], list)
    assert result["from_cache"] is False


def test_research_cache_hits_on_second_call():
    from gtm_engine.agents import research_agent

    db.init_db()
    lead = {"company": "Bramble Legal Tech", "domain": "bramblelegal.com",
            "raw": {"industry": "Legal Tech"}, "company_size_bucket": "small (20-75)"}

    first = research_agent.research(lead)
    second = research_agent.research(lead)

    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["summary"] == first["summary"]


def test_full_pipeline_still_runs_end_to_end_after_grounding():
    results = run_pipeline(max_leads=1)
    assert len(results) == 1
    assert results[0]["pain_points"]


def test_funnel_counts_track_sourced_and_sent():
    run_pipeline(max_leads=2)
    funnel = db.funnel_counts()
    assert funnel["lead_sourced"] == 2
    assert funnel["email_sent"] == 2
    # nothing has "opened" until an ESP webhook (or the demo simulator) fires
    assert funnel["email_opened"] == 0


def test_llm_usage_is_tracked_with_cost():
    run_pipeline(max_leads=1)
    summary = db.cost_summary()
    assert summary["total_input_tokens"] > 0
    assert summary["total_output_tokens"] > 0
    assert summary["total_cost_usd"] >= 0
    # research, pain_point, email, linkedin, and follow-up drafts should all appear
    assert "research" in summary["by_agent"]
    assert "email" in summary["by_agent"]
    assert "linkedin" in summary["by_agent"]
    avg = db.cost_per_lead()
    assert avg >= 0


def test_track_event_endpoint_records_event():
    run_pipeline(max_leads=1)
    leads = db.all_leads()
    lead_id = leads[0]["id"]
    db.record_event(lead_id, "meeting_booked")
    funnel = db.funnel_counts()
    assert funnel["meeting_booked"] == 1


def test_reply_classifier_categorizes_correctly():
    from gtm_engine.agents import reply_classifier_agent

    cases = {
        "Please unsubscribe me, stop emailing me.": "unsubscribe",
        "I'm currently out of office until next week (OOO auto-reply).": "out_of_office",
        "Not interested, please stop reaching out.": "not_interested",
        "Sure, let's book a call — here's my calendly.": "meeting_request",
        "Not right now, check back next quarter.": "not_now",
        "This is interesting, tell me more.": "interested",
        "What exactly does this do?": "needs_more_info",
    }
    for text, expected in cases.items():
        result = reply_classifier_agent.classify(text, {"first_name": "Test"})
        assert result["classification"] == expected, f"{text!r} -> {result}"


def test_adaptive_followup_cancels_on_not_interested():
    from gtm_engine.agents import followup_agent

    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]
    before = [o for o in db.all_outreach() if o["lead_id"] == lead_id and o["status"] == "scheduled"]
    assert len(before) > 0, "there should be scheduled follow-ups before the reply"

    outcome = followup_agent.handle_reply(lead_id, "Not interested, please stop reaching out.")
    assert outcome["classification"] == "not_interested"
    assert outcome["action"] == "cancel"
    assert outcome["outreach_affected"] == len(before)

    after = [o for o in db.all_outreach() if o["lead_id"] == lead_id and o["status"] == "scheduled"]
    assert len(after) == 0
    assert db.get_lead(lead_id)["status"] == "disqualified"


def test_adaptive_followup_reschedules_on_not_now():
    from gtm_engine.agents import followup_agent

    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]
    before = {o["id"]: o["scheduled_for"] for o in db.all_outreach()
              if o["lead_id"] == lead_id and o["status"] == "scheduled"}

    outcome = followup_agent.handle_reply(lead_id, "Not right now, check back next quarter.")
    assert outcome["classification"] == "not_now"
    assert outcome["action"] == "reschedule:30"

    after = {o["id"]: o["scheduled_for"] for o in db.all_outreach()
              if o["lead_id"] == lead_id and o["id"] in before}
    for oid, new_time in after.items():
        assert new_time > before[oid], "scheduled_for should have been pushed out"
    assert db.get_lead(lead_id)["status"] == "nurture"


def test_adaptive_followup_keeps_schedule_on_needs_more_info():
    from gtm_engine.agents import followup_agent

    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]
    before = [o for o in db.all_outreach() if o["lead_id"] == lead_id and o["status"] == "scheduled"]

    outcome = followup_agent.handle_reply(lead_id, "What exactly does this do?")
    assert outcome["classification"] == "needs_more_info"
    assert outcome["action"] == "keep"

    after = [o for o in db.all_outreach() if o["lead_id"] == lead_id and o["status"] == "scheduled"]
    assert len(after) == len(before), "nothing should be cancelled or rescheduled"
    assert db.get_lead(lead_id)["status"] == "engaged"


def test_track_event_endpoint_triggers_adaptive_followup():
    from gtm_engine.api import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]

    resp = client.post("/events/track", json={
        "lead_id": lead_id, "event_type": "email_replied",
        "reply_text": "Sure, let's book a call — here's my calendly.",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["adaptive_followup"]["classification"] == "meeting_request"
    assert body["adaptive_followup"]["action"] == "cancel"


def test_email_variants_are_stylistically_distinct():
    from gtm_engine.agents import email_agent

    lead = {"first_name": "Priya", "company": "Bramble Legal Tech", "title": "Head of Sales"}
    variants = email_agent.generate_variants(lead, ["Manual document review slows deals"])
    assert set(variants.keys()) == {"A", "B"}
    assert variants["A"]["body"] != variants["B"]["body"]
    assert "{first_name}" not in variants["A"]["body"]
    assert "{first_name}" not in variants["B"]["body"]


def test_orchestrator_assigns_a_variant_per_lead():
    run_pipeline(max_leads=3)
    outreach = [o for o in db.all_outreach() if o["channel"] == "email" and o["sequence_step"] == 0]
    assert len(outreach) == 3
    for row in outreach:
        assert row["variant"] in ("A", "B")


def test_variant_performance_computes_rates():
    from gtm_engine.agents import ab_test

    run_pipeline(max_leads=4)
    leads = db.all_leads()

    # Force a known split so the math is checkable: leads[0:2] -> A, leads[2:4] -> B
    for i, lead in enumerate(leads):
        variant = "A" if i < 2 else "B"
        with db.engine.begin() as conn:
            conn.execute(
                db.outreach.update()
                .where(db.outreach.c.lead_id == lead["id"], db.outreach.c.sequence_step == 0,
                       db.outreach.c.channel == "email")
                .values(variant=variant)
            )

    # Variant A: both leads open, one replies. Variant B: neither opens.
    db.record_event(leads[0]["id"], "email_opened")
    db.record_event(leads[1]["id"], "email_opened")
    db.record_event(leads[0]["id"], "email_replied")

    performance = db.variant_performance()
    assert performance["A"]["sent"] == 2
    assert performance["A"]["opened"] == 2
    assert performance["A"]["open_rate"] == 1.0
    assert performance["A"]["reply_rate"] == 0.5
    assert performance["B"]["sent"] == 2
    assert performance["B"]["opened"] == 0
    assert performance["B"]["open_rate"] == 0.0


def test_ab_test_does_not_promote_below_min_sample():
    from gtm_engine.agents import ab_test

    run_pipeline(max_leads=2)  # far below MIN_SAMPLE_PER_VARIANT
    decision = ab_test.evaluate_and_promote()
    assert decision["decided"] is False
    assert db.get_active_winner() is None


def test_ab_test_promotes_clear_winner_with_enough_samples():
    from gtm_engine.agents import ab_test

    run_pipeline(max_leads=ab_test.MIN_SAMPLE_PER_VARIANT * 2 + 4)
    leads = db.all_leads()
    half = len(leads) // 2

    with db.engine.begin() as conn:
        for i, lead in enumerate(leads):
            variant = "A" if i < half else "B"
            conn.execute(
                db.outreach.update()
                .where(db.outreach.c.lead_id == lead["id"], db.outreach.c.sequence_step == 0,
                       db.outreach.c.channel == "email")
                .values(variant=variant)
            )

    # Variant A replies at a much higher rate than B, with plenty of samples.
    for lead in leads[:half]:
        db.record_event(lead["id"], "email_replied")

    decision = ab_test.evaluate_and_promote()
    assert decision["decided"] is True
    assert decision["winner"] == "A"
    assert db.get_active_winner()["variant"] == "A"


def test_assign_variant_favors_promoted_winner():
    from gtm_engine.agents import ab_test

    db.init_db()
    db.set_winner("A", "reply_rate", 0.5)
    assignments = [ab_test.assign_variant() for _ in range(200)]
    a_share = assignments.count("A") / len(assignments)
    # Should be roughly EXPLOIT_WINNER_PROBABILITY (0.8), allow generous slack
    assert a_share > 0.6, f"expected winner to dominate assignment, got {a_share}"


def test_research_carries_sources_and_confidence():
    from gtm_engine.agents import research_agent

    db.init_db()
    lead = {"company": "Bramble Legal Tech", "domain": "bramblelegal-evidence.com",
            "raw": {"industry": "Legal Tech"}, "company_size_bucket": "small (20-75)"}
    result = research_agent.research(lead)

    assert result["sources"], "mock search should return at least one source"
    for s in result["sources"]:
        assert s["title"] and s["url"]
    assert result["confidence"] in ("low", "medium", "high")


def test_reflection_agent_scores_and_flags_generic_copy():
    from gtm_engine.agents import reflection_agent

    on_point = {"subject": "x", "body": "manual document review slows deal velocity for your team"}
    generic = {"subject": "x", "body": "just checking in, hope you're well, let me know your thoughts"}

    good = reflection_agent.judge(on_point, "Manual document review slows down deal velocity")
    bad = reflection_agent.judge(generic, "Manual document review slows down deal velocity")

    assert good["approved"] is True
    assert bad["approved"] is False
    assert bad["score"] < good["score"]


def test_pipeline_records_trace_spans_per_lead():
    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]
    trace = db.trace_for_lead(lead_id)
    agents_seen = {span["agent"] for span in trace}
    assert "research" in agents_seen
    assert "pain_point" in agents_seen
    assert "email" in agents_seen
    assert "reflect" in agents_seen
    assert "linkedin" in agents_seen
    for span in trace:
        assert span["duration_ms"] >= 0
        assert span["success"] == 1


def test_latency_summary_aggregates_across_leads():
    run_pipeline(max_leads=2)
    summary = db.latency_summary()
    assert "research" in summary
    assert summary["research"]["calls"] >= 2
    assert summary["research"]["avg_ms"] >= 0


def test_company_summary_aggregates_leads():
    run_pipeline(max_leads=3)
    companies = db.company_summary()
    assert len(companies) == 3
    for c in companies:
        assert c["lead_count"] == 1
        assert c["latest_status"] == "sequenced"


def test_all_research_cache_lists_entries():
    run_pipeline(max_leads=2)
    cache_rows = db.all_research_cache()
    assert len(cache_rows) == 2
    for row in cache_rows:
        assert row["summary"]


def test_lead_detail_includes_everything():
    run_pipeline(max_leads=1)
    lead_id = db.all_leads()[0]["id"]
    detail = db.lead_detail(lead_id)
    assert detail["lead"]["id"] == lead_id
    assert detail["research"] is not None
    assert detail["pain_points"] is not None
    assert len(detail["outreach"]) >= 2  # email + linkedin, plus scheduled follow-ups
    assert len(detail["events"]) >= 2    # lead_sourced + email_sent
    assert len(detail["trace"]) >= 4     # research/pain_point/email/reflect/linkedin spans


def test_settings_endpoint_returns_safe_config():
    from gtm_engine.api import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "mock_mode" in body
    assert "sender_name" in body
    assert "database_url_kind" in body


def test_followup_agent_processes_due_items():
    import datetime
    from gtm_engine.agents import followup_agent

    run_pipeline(max_leads=1)

    # Force all scheduled follow-ups to be "due" right now
    with db.engine.begin() as conn:
        conn.execute(
            db.outreach.update().values(scheduled_for=datetime.datetime.now(datetime.timezone.utc))
        )

    sent = []
    def fake_sender(row):
        sent.append(row["id"])
        return True

    results = followup_agent.run_due_followups(fake_sender)
    assert len(results) >= 1
    assert all(r["status"] == "sent" for r in results)
