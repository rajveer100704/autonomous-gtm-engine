"""Pure statistics, promotion gates, and policy assignment tests."""
import pytest
from gtm_engine.optimization.statistics import wilson_interval, newcombe_difference_interval, evaluate_promotion_gate
from gtm_engine.optimization.policy import RandomizedBaselinePolicy, GlobalBestPolicy, SegmentBestPolicy
from gtm_engine.optimization.feedback import OutcomeFeedbackService


def test_wilson_interval_edge_cases():
    assert wilson_interval(0, 0).rate == 0.0
    assert wilson_interval(0, 0).ci_upper == 1.0
    assert wilson_interval(0, 20).ci_lower == 0.0
    assert wilson_interval(20, 20).ci_upper == 1.0
    interval = wilson_interval(5, 20)
    assert 0.0 < interval.ci_lower < interval.rate < interval.ci_upper < 1.0


def test_difference_interval_orientation():
    a = wilson_interval(80, 100)
    b = wilson_interval(20, 100)
    difference = newcombe_difference_interval(a, b)
    assert difference.favors == "A"
    assert difference.ci_lower > 0
    reverse = newcombe_difference_interval(b, a)
    assert reverse.favors == "B"
    assert reverse.ci_upper < 0


def test_small_sample_never_passes_gate():
    decision = evaluate_promotion_gate({"sent": 10, "replied": 10}, {"sent": 10, "replied": 0}, min_sample_per_variant=30)
    assert not decision.passed
    assert decision.status == "insufficient_evidence"


def test_interval_overlapping_zero_blocks_promotion():
    decision = evaluate_promotion_gate({"sent": 100, "replied": 20}, {"sent": 100, "replied": 19}, min_sample_per_variant=30)
    assert not decision.passed
    assert decision.status == "no_clear_winner"


def test_practical_lift_gate_blocks_small_difference():
    decision = evaluate_promotion_gate({"sent": 1000, "replied": 202}, {"sent": 1000, "replied": 200}, min_sample_per_variant=30, min_practical_lift=0.02)
    assert not decision.passed


def test_bounce_guardrail_blocks_materially_worse_candidate():
    # Strong reply lift but materially higher bounce risk must block the candidate.
    decision = evaluate_promotion_gate(
        {"sent": 1000, "replied": 500, "bounced": 120, "unsubscribed": 0},
        {"sent": 1000, "replied": 100, "bounced": 10, "unsubscribed": 0},
        min_sample_per_variant=30, min_practical_lift=0.02, max_safety_risk_excess=0.03,
    )
    assert not decision.passed
    assert decision.status == "safety_guardrail_failed"


def test_baseline_policy_logs_half_propensity():
    assignments = [RandomizedBaselinePolicy.assign(segment_key="technical").to_dict() for _ in range(20)]
    assert all(item["propensity"] == 0.5 for item in assignments)
    assert all(item["policy_name"] == "randomized_baseline_v1" for item in assignments)


def test_global_and_segment_policies_log_selected_arm_propensity():
    global_assignments = [GlobalBestPolicy.assign(active_winner="A").to_dict() for _ in range(100)]
    assert all(item["propensity"] in (0.8, 0.2) for item in global_assignments)
    segment_assignments = [SegmentBestPolicy.assign({"technical": "B"}, segment_key="technical").to_dict() for _ in range(100)]
    assert all(item["propensity"] in (0.8, 0.2) for item in segment_assignments)


def test_missing_logged_propensity_is_not_evaluable_for_off_policy_comparison():
    result = OutcomeFeedbackService.off_policy_evaluation(
        [{"decision_id": 1, "action": "A", "replied": 1}],
        target_policy_name="randomized_baseline_v1",
    )
    assert result["status"] == "not_evaluable_for_causal_policy_comparison"


def test_recovered_invalid_propensity_is_not_evaluable_for_off_policy_comparison():
    result = OutcomeFeedbackService.off_policy_evaluation(
        [{"decision_id": 2, "action": "A", "replied": 1, "assignment_probability": .5, "propensity_valid": False}],
        target_policy_name="randomized_baseline_v1",
    )
    assert result["status"] == "not_evaluable_for_causal_policy_comparison"


def test_large_samples_can_clear_gate_when_safety_uncertainty_is_small():
    decision = evaluate_promotion_gate(
        {"sent": 1500, "replied": 750, "bounced": 0, "unsubscribed": 0},
        {"sent": 1500, "replied": 300, "bounced": 0, "unsubscribed": 0},
        min_sample_per_variant=30, min_practical_lift=0.02, max_safety_risk_excess=0.03,
    )
    assert decision.passed
    assert decision.winner == "A"
    assert decision.safety_details["candidate_vs_competitor"]["uncertainty_included"] is True


def test_global_best_off_policy_comparison_requires_target_action():
    result = OutcomeFeedbackService.off_policy_evaluation(
        [{"decision_id": 3, "action": "A", "replied": 1, "assignment_probability": .5}],
        target_policy_name="global_best_v1",
    )
    assert result["status"] == "not_evaluable_for_causal_policy_comparison"
