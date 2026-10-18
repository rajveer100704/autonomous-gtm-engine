"""Determinism, held-out separation, and negative-control simulator tests."""
from gtm_engine.optimization.simulator import evaluate_scenario
from gtm_engine.optimization.report import build_report


def test_simulator_is_deterministic_for_same_seed():
    a = evaluate_scenario(seed=123, train_size=800, evaluation_size=500)
    b = evaluate_scenario(seed=123, train_size=800, evaluation_size=500)
    assert a == b


def test_all_policies_score_same_heldout_population_and_report_oracle():
    result = evaluate_scenario(seed=234, train_size=2000, evaluation_size=1000)
    assert result["train_and_evaluation_disjoint"] is True
    assert result["heldout_population_shared"] is True
    assert set(result["policies"]) == {
        "randomized_baseline_v1", "global_best_v1", "segment_best_v1", "oracle_upper_bound"
    }
    assert all(p["sample_size"] == 1000 for p in result["policies"].values())
    assert result["policies"]["oracle_upper_bound"]["is_deployable"] is False


def test_segment_policy_learns_heterogeneous_preferences_with_enough_training_data():
    result = evaluate_scenario(seed=20261019, train_size=8000, evaluation_size=2000)
    winners = result["learned_policy"]["segment_winners"]
    assert winners.get("technical") == "A"
    assert winners.get("operations") == "B"
    assert result["policies"]["segment_best_v1"]["reply_rate"] > result["policies"]["randomized_baseline_v1"]["reply_rate"]


def test_equal_effect_negative_control_does_not_force_policy_uplift():
    results = [evaluate_scenario(seed=400 + i, train_size=2500, evaluation_size=1200, equal_effect=True) for i in range(5)]
    deltas = [r["policies"]["segment_best_v1"]["difference_vs_baseline"] for r in results]
    assert abs(sum(deltas) / len(deltas)) < 0.04


def test_report_has_seeded_positive_and_negative_controls():
    report = build_report(seed=7, train_size=500, evaluation_size=300, scenarios=2)
    assert report["seed"] == 7
    assert report["scenario_count"] == 2
    assert len(report["positive_control_scenarios"]) == 2
    assert report["equal_effect_negative_control"]["scenario_count"] == 2
    assert "synthetic" in " ".join(report["caveats"]).lower()


def test_train_and_holdout_have_distinct_ids():
    from gtm_engine.optimization.simulator import generate_population
    train = generate_population(100, 11, id_prefix="train")
    holdout = generate_population(100, 12, id_prefix="holdout")
    assert not ({r["lead_id"] for r in train} & {r["lead_id"] for r in holdout})
