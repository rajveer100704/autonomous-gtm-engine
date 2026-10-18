"""Database, attribution, mature-cohort, and API compatibility tests for V5."""
import datetime as dt

import pytest
from gtm_engine.crm import db

UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db.recreate_db_engine(f"sqlite:///{tmp_path / 'v5_feedback.db'}")
    db.init_db()
    yield
    db.engine.dispose()


def create_exposure(variant="A", sent_days_ago=30, segment="all", simulated=False, lead_id=None):
    now = dt.datetime.now(UTC)
    if lead_id is None:
        lead_id = db.insert_lead({"full_name": "Test Lead", "title": "CTO", "company": "Example",
                                  "domain": "example.test", "email": "test@example.test", "raw": {}})
    sent_at = now - dt.timedelta(days=sent_days_ago)
    outreach_id = db.save_outreach(
        lead_id=lead_id, channel="email", step=0, subject="Hello", body="Test", variant=variant,
        scheduled_for=sent_at, status="drafted",
    )
    db.update_outreach_status(outreach_id, "sent", sent_at=sent_at)
    decision_id = db.record_decision(
        lead_id=lead_id, action=variant, action_value="direct" if variant == "A" else "curiosity",
        assignment_probability=0.5, policy_name="randomized_baseline_v1", decision_key=f"decision-{outreach_id}",
        outreach_id=outreach_id, channel="email", sequence_step=0, decision_type="email_style",
        segment_key=segment, context={"is_simulated_exposure": simulated},
    )
    return lead_id, outreach_id, decision_id, sent_at


def test_init_db_and_new_tables_are_idempotent():
    db.init_db()
    db.init_db()
    assert db.all_learning_decisions() == []
    assert db.all_learning_outcomes() == []


def test_decision_retry_is_idempotent_and_key_reuse_must_match():
    lead, outreach_id, decision, _ = create_exposure()
    assert db.record_decision(
        lead_id=lead, action="A", action_value="direct", assignment_probability=0.5,
        policy_name="randomized_baseline_v1", decision_key=f"decision-{outreach_id}", outreach_id=outreach_id,
        channel="email", sequence_step=0, decision_type="email_style", segment_key="all",
    ) == decision
    with pytest.raises(ValueError, match="decision_key"):
        db.record_decision(lead_id=lead, action="B", action_value="curiosity", assignment_probability=0.5,
                           policy_name="randomized_baseline_v1", decision_key=f"decision-{outreach_id}",
                           outreach_id=outreach_id, channel="email", sequence_step=0)


def test_outcome_is_attributed_exactly_and_idempotent():
    lead, outreach_id, decision_id, sent_at = create_exposure()
    args = dict(lead_id=lead, outreach_id=outreach_id, decision_id=decision_id,
                event_type="email_replied", event_key="provider-event-1",
                occurred_at=sent_at + dt.timedelta(days=1))
    first = db.record_learning_outcome(**args)
    second = db.record_learning_outcome(**args)
    assert first["status"] == "recorded"
    assert second["status"] == "duplicate"
    assert len(db.all_learning_outcomes()) == 1
    data = db.get_attribution_dataset()
    assert data["variant_stats"]["A"]["mature_sent"] == 1
    assert data["variant_stats"]["A"]["replied"] == 1


def test_outcome_rejects_cross_touch_and_event_key_reuse():
    lead, outreach_id, decision_id, sent_at = create_exposure("A")
    other_outreach_id = db.save_outreach(lead_id=lead, channel="email", step=0, subject="Other", body="Other",
                                         variant="B", scheduled_for=sent_at, status="drafted")
    db.update_outreach_status(other_outreach_id, "sent", sent_at=sent_at)
    with pytest.raises(ValueError, match="exact outreach"):
        db.record_learning_outcome(lead_id=lead, outreach_id=other_outreach_id, decision_id=decision_id,
                                   event_type="email_replied", event_key="bad-link")
    db.record_learning_outcome(lead_id=lead, outreach_id=outreach_id, decision_id=decision_id,
                               event_type="email_replied", event_key="same-key",
                               occurred_at=sent_at + dt.timedelta(days=1))
    with pytest.raises(ValueError, match="different outcome"):
        db.record_learning_outcome(lead_id=lead, outreach_id=outreach_id, decision_id=decision_id,
                                   event_type="meeting_booked", event_key="same-key",
                                   occurred_at=sent_at + dt.timedelta(days=1))


def test_legacy_lead_only_events_do_not_enter_learning_dataset():
    lead, _, _, _ = create_exposure()
    db.record_event(lead, "email_replied")
    data = db.get_attribution_dataset()
    assert data["variant_stats"]["A"]["replied"] == 0
    assert data["unattributed_events_count"] == 1


def test_multiple_touches_on_one_lead_do_not_cross_attribute_outcomes():
    lead, outreach_a, decision_a, sent_a = create_exposure("A")
    now = dt.datetime.now(UTC)
    outreach_b = db.save_outreach(lead_id=lead, channel="email", step=0, subject="Second", body="Second",
                                  variant="B", scheduled_for=sent_a, status="drafted")
    db.update_outreach_status(outreach_b, "sent", sent_at=sent_a)
    decision_b = db.record_decision(
        lead_id=lead, action="B", action_value="curiosity", assignment_probability=0.5,
        policy_name="randomized_baseline_v1", decision_key=f"decision-{outreach_b}", outreach_id=outreach_b,
        channel="email", sequence_step=0, decision_type="email_style", segment_key="all",
    )
    db.record_learning_outcome(lead_id=lead, outreach_id=outreach_b, decision_id=decision_b,
                               event_type="email_replied", event_key="reply-second-touch",
                               occurred_at=sent_a + dt.timedelta(days=1))
    dataset = db.get_attribution_dataset()
    assert dataset["variant_stats"]["A"]["replied"] == 0
    assert dataset["variant_stats"]["B"]["replied"] == 1


def test_immature_outcome_is_recorded_but_not_counted_until_maturity():
    lead, outreach_id, decision_id, sent_at = create_exposure(sent_days_ago=2)
    db.record_learning_outcome(lead_id=lead, outreach_id=outreach_id, decision_id=decision_id,
                               event_type="email_replied", event_key="recent-reply",
                               occurred_at=sent_at + dt.timedelta(days=1))
    now = dt.datetime.now(UTC)
    current = db.get_attribution_dataset(now=now)
    assert current["variant_stats"]["A"]["immature_sent"] == 1
    assert current["variant_stats"]["A"]["replied"] == 0
    future = db.get_attribution_dataset(now=now + dt.timedelta(days=20))
    assert future["variant_stats"]["A"]["mature_sent"] == 1
    assert future["variant_stats"]["A"]["replied"] == 1


def test_outcome_outside_attribution_window_is_excluded():
    lead, outreach_id, decision_id, sent_at = create_exposure(sent_days_ago=40)
    db.record_learning_outcome(lead_id=lead, outreach_id=outreach_id, decision_id=decision_id,
                               event_type="email_replied", event_key="late-reply",
                               occurred_at=sent_at + dt.timedelta(days=20))
    data = db.get_attribution_dataset()
    assert data["variant_stats"]["A"]["replied"] == 0
    assert data["excluded_outcomes_invalid_or_outside_window"] == 1


def test_simulated_exposures_and_outcomes_are_excluded_by_default():
    lead, outreach_id, decision_id, sent_at = create_exposure(simulated=True)
    db.record_learning_outcome(lead_id=lead, outreach_id=outreach_id, decision_id=decision_id,
                               event_type="email_replied", event_key="sim-reply",
                               occurred_at=sent_at + dt.timedelta(days=1), is_simulated=True, source="simulator")
    real_data = db.get_attribution_dataset()
    assert real_data["sent_exposures"] == 0
    assert real_data["excluded_simulated_exposures"] == 1
    simulated_data = db.get_attribution_dataset(include_simulated=True)
    assert simulated_data["sent_exposures"] == 1
    assert simulated_data["variant_stats"]["A"]["replied"] == 1


def test_drafts_are_not_exposure_denominators():
    lead_id = db.insert_lead({"full_name": "Draft", "company": "Example", "raw": {}})
    oid = db.save_outreach(lead_id=lead_id, channel="email", step=0, subject="Draft", body="Draft",
                           variant="A", scheduled_for=dt.datetime.now(UTC), status="drafted")
    db.record_decision(lead_id=lead_id, action="A", action_value="direct", assignment_probability=.5,
                       policy_name="randomized_baseline_v1", decision_key="draft-only", outreach_id=oid,
                       channel="email", sequence_step=0)
    assert db.get_attribution_dataset()["sent_exposures"] == 0


def test_legacy_track_event_request_stays_compatible_and_unattributed():
    from gtm_engine import api
    lead, _, _, _ = create_exposure()
    result = api.track_event(api.TrackEventRequest(lead_id=lead, event_type="email_replied"))
    assert result["status"] == "recorded"
    assert result["attribution_status"] == "unattributed_not_used_for_learning"


def test_explicit_api_attribution_is_recorded_and_mismatch_rejected():
    from fastapi import HTTPException
    from gtm_engine import api
    lead, outreach_id, decision_id, sent_at = create_exposure(sent_days_ago=2)
    result = api.track_event(api.TrackEventRequest(
        lead_id=lead, event_type="email_replied", outreach_id=outreach_id, decision_id=decision_id,
        event_key="api-reply", occurred_at=sent_at + dt.timedelta(days=1),
    ))
    assert result["attribution_status"] == "recorded"
    with pytest.raises(HTTPException) as err:
        api.track_event(api.TrackEventRequest(lead_id=lead, event_type="email_replied",
                                              outreach_id=outreach_id + 999, decision_id=decision_id))
    assert err.value.status_code in (400, 404)


def test_get_recommendation_api_is_read_only_and_returns_insufficient_evidence():
    from gtm_engine import api
    before = db.get_validated_active_winner()
    summary = api.analytics_outcome_feedback(14)
    recommendation = api.optimization_recommendation("all", 14, False)
    after = db.get_validated_active_winner()
    assert summary["cohort"]["mature_exposures"] == 0
    assert recommendation["status"] == "insufficient_evidence"
    assert before == after


def test_langgraph_content_node_records_exact_decision_and_reuses_it_on_retry():
    from gtm_engine.graph.nodes import content_node
    lead_id = db.insert_lead({"full_name": "Graph Test", "title": "CTO", "company": "GraphCo",
                              "domain": "graphco.test", "email": "graph@example.test", "raw": {"industry": "Software"}})
    lead = {"id": lead_id, "full_name": "Graph Test", "first_name": "Graph", "last_name": "Test",
            "title": "CTO", "company": "GraphCo", "domain": "graphco.test", "email": "graph@example.test",
            "linkedin_url": "https://example.test/profile", "raw": {"industry": "Software"}}
    state = {"current_lead": lead, "pain_points": ["manual workflows create delays"], "memory_context": ""}
    first = content_node(state)
    second = content_node(state)
    decisions = [d for d in db.all_learning_decisions() if d["lead_id"] == lead_id]
    email_touches = [o for o in db.all_outreach() if o["lead_id"] == lead_id and o["channel"] == "email" and o["sequence_step"] == 0]
    linkedins = [o for o in db.all_outreach() if o["lead_id"] == lead_id and o["channel"] == "linkedin" and o["sequence_step"] == 0]
    assert len(decisions) == 1
    assert len(email_touches) == 1
    assert len(linkedins) == 1
    assert decisions[0]["outreach_id"] == first["email_copy"]["outreach_id"] == second["email_copy"]["outreach_id"]
    assert first["email_copy"]["decision_id"] == second["email_copy"]["decision_id"]


def test_explicit_evaluation_promotes_with_strong_real_attributed_evidence_idempotently():
    from sqlalchemy import func, select
    from gtm_engine.agents import ab_test

    # Synthetic deterministic test fixture representing adequately sampled, mature,
    # explicitly linked records. These are not included in the benchmark report.
    for variant, n, replies in (("A", 260, 190), ("B", 260, 60)):
        for index in range(n):
            lead, outreach_id, decision_id, sent_at = create_exposure(variant, sent_days_ago=30)
            if index < replies:
                db.record_learning_outcome(
                    lead_id=lead, outreach_id=outreach_id, decision_id=decision_id,
                    event_type="email_replied", event_key=f"fixture-reply-{variant}-{index}",
                    occurred_at=sent_at + dt.timedelta(days=1), source="test_fixture", is_simulated=False,
                )
    first = ab_test.evaluate_and_promote()
    second = ab_test.evaluate_and_promote()
    assert first["decided"] is True
    assert first["winner"] == "A"
    assert second["decided"] is True
    assert db.get_validated_active_winner()["variant"] == "A"
    with db.engine.begin() as conn:
        legacy_winner_rows = conn.execute(select(func.count()).select_from(db.ab_winner)).scalar_one()
        validated_rows = conn.execute(select(func.count()).select_from(db.learning_promotions)).scalar_one()
    assert legacy_winner_rows == 1
    assert validated_rows == 1


def test_linear_pipeline_logs_decision_to_exact_outreach_and_marks_mock_exposure():
    from gtm_engine.orchestrator import run_pipeline
    run_pipeline(max_leads=1)
    all_decisions = db.all_learning_decisions()
    assert len(all_decisions) == 1
    decision = all_decisions[0]
    outreach = next(o for o in db.all_outreach() if o["id"] == decision["outreach_id"])
    assert outreach["lead_id"] == decision["lead_id"]
    assert outreach["channel"] == "email"
    assert decision["action"] == outreach["variant"]
    assert decision["assignment_probability"] in (0.2, 0.5, 0.8)
    assert decision["context_json"]["is_simulated_exposure"] is True
    # Mock sends remain visible to legacy funnel analytics but are excluded from real learning.
    dataset = db.get_attribution_dataset()
    assert dataset["sent_exposures"] == 0
    assert dataset["excluded_simulated_exposures"] == 1
