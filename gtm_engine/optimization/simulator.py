"""Deterministic local-only simulator for outcome-feedback policy evaluation.

All outcomes are synthetic. Policies are trained on a generated training cohort and
scored on a separate held-out cohort. All candidate policies use the same potential
outcomes for each held-out lead; the oracle is reported only as a non-deployable bound.
"""
from __future__ import annotations

import random
from typing import Any

from gtm_engine.optimization.statistics import (
    evaluate_promotion_gate, wilson_interval, newcombe_difference_interval,
)

SIMULATOR_SCHEMA_VERSION = "1.0"
DEFAULT_SEED = 20261019
DEFAULT_ATTRIBUTION_WINDOW_DAYS = 14


def _probabilities(segment: str, equal_effect: bool) -> dict[str, dict[str, float]]:
    if equal_effect:
        reply = 0.22 if segment == "technical" else 0.20
        return {
            "A": {"reply": reply, "meeting": reply * 0.35, "won": reply * 0.07, "bounce": 0.025, "unsubscribe": 0.018},
            "B": {"reply": reply, "meeting": reply * 0.35, "won": reply * 0.07, "bounce": 0.025, "unsubscribe": 0.018},
        }
    if segment == "technical":
        return {
            "A": {"reply": 0.32, "meeting": 0.32 * 0.38, "won": 0.32 * 0.09, "bounce": 0.025, "unsubscribe": 0.015},
            "B": {"reply": 0.15, "meeting": 0.15 * 0.35, "won": 0.15 * 0.07, "bounce": 0.028, "unsubscribe": 0.020},
        }
    return {
        "A": {"reply": 0.14, "meeting": 0.14 * 0.33, "won": 0.14 * 0.06, "bounce": 0.030, "unsubscribe": 0.022},
        "B": {"reply": 0.29, "meeting": 0.29 * 0.38, "won": 0.29 * 0.09, "bounce": 0.025, "unsubscribe": 0.016},
    }


def generate_population(size: int, seed: int, equal_effect: bool = False, id_prefix: str = "lead") -> list[dict[str, Any]]:
    """Generate fixed potential outcomes for both actions for every lead."""
    rng = random.Random(seed)
    population = []
    for index in range(size):
        segment = "technical" if rng.random() < 0.5 else "operations"
        probs = _probabilities(segment, equal_effect)
        potential = {}
        for action in ("A", "B"):
            reply = int(rng.random() < probs[action]["reply"])
            meeting_conditional = (probs[action]["meeting"] / probs[action]["reply"]) if probs[action]["reply"] else 0.0
            won_conditional = (probs[action]["won"] / probs[action]["meeting"]) if probs[action]["meeting"] else 0.0
            meeting = int(bool(reply) and rng.random() < meeting_conditional)
            won = int(bool(meeting) and rng.random() < won_conditional)
            potential[action] = {
                "reply": reply, "meeting": meeting, "won": won,
                "bounce": int(rng.random() < probs[action]["bounce"]),
                "unsubscribe": int(rng.random() < probs[action]["unsubscribe"]),
            }
        population.append({"lead_id": f"{id_prefix}-{index + 1}", "segment": segment, "potential": potential})
    return population


def _arm_stats(rows: list[dict[str, Any]], action: str) -> dict[str, int]:
    selected = [r for r in rows if r["action"] == action]
    return {
        "sent": len(selected),
        "replied": sum(r["outcome"]["reply"] for r in selected),
        "meetings": sum(r["outcome"]["meeting"] for r in selected),
        "won": sum(r["outcome"]["won"] for r in selected),
        "bounced": sum(r["outcome"]["bounce"] for r in selected),
        "unsubscribed": sum(r["outcome"]["unsubscribe"] for r in selected),
    }


def _learn(training_population: list[dict[str, Any]], seed: int, min_sample: int = 30) -> dict[str, Any]:
    rng = random.Random(seed)
    assigned = []
    for lead in training_population:
        action = "A" if rng.random() < 0.5 else "B"
        assigned.append({"segment": lead["segment"], "action": action,
                         "outcome": lead["potential"][action], "assignment_probability": 0.5})
    global_gate = evaluate_promotion_gate(_arm_stats(assigned, "A"), _arm_stats(assigned, "B"), min_sample_per_variant=min_sample)
    global_winner = global_gate.winner if global_gate.passed else None
    segment_winners: dict[str, str] = {}
    segment_gates = {}
    for segment in ("technical", "operations"):
        cohort = [r for r in assigned if r["segment"] == segment]
        gate = evaluate_promotion_gate(_arm_stats(cohort, "A"), _arm_stats(cohort, "B"), min_sample_per_variant=min_sample)
        segment_gates[segment] = {"status": gate.status, "winner": gate.winner, "reason": gate.reason}
        if gate.passed and gate.winner:
            segment_winners[segment] = gate.winner
    return {
        "global_winner": global_winner,
        "global_gate": {"status": global_gate.status, "winner": global_gate.winner, "reason": global_gate.reason},
        "segment_winners": segment_winners,
        "segment_gates": segment_gates,
        "training_arm_counts": {a: _arm_stats(assigned, a) for a in ("A", "B")},
        "training_size": len(training_population),
    }


def _choose(policy: str, segment: str, learned: dict[str, Any], truth_best: dict[str, str], rng: random.Random) -> str:
    if policy == "randomized_baseline_v1":
        return "A" if rng.random() < 0.5 else "B"
    if policy == "global_best_v1":
        winner = learned["global_winner"]
        if winner is None:
            return "A" if rng.random() < 0.5 else "B"
        return winner if rng.random() < 0.8 else ("B" if winner == "A" else "A")
    if policy == "segment_best_v1":
        winner = learned["segment_winners"].get(segment) or learned["global_winner"]
        if winner is None:
            return "A" if rng.random() < 0.5 else "B"
        return winner if rng.random() < 0.8 else ("B" if winner == "A" else "A")
    if policy == "oracle_upper_bound":
        return truth_best[segment]
    raise ValueError(f"Unsupported policy: {policy}")


def _bootstrap_difference(paired_differences: list[float], seed: int, samples: int = 500) -> list[float]:
    if not paired_differences:
        return [0.0, 0.0]
    rng = random.Random(seed)
    n = len(paired_differences)
    means = []
    for _ in range(samples):
        total = sum(paired_differences[rng.randrange(n)] for _ in range(n))
        means.append(total / n)
    means.sort()
    return [round(means[int(0.025 * (samples - 1))], 6), round(means[int(0.975 * (samples - 1))], 6)]


def _heldout_evidence_gate(policy: str, policy_rows: list[dict[str, Any]], baseline_rows: list[dict[str, Any]],
                           reply_difference_ci: list[float], min_practical_lift: float = 0.02,
                           max_safety_excess: float = 0.03) -> dict[str, Any]:
    if policy == "randomized_baseline_v1":
        return {"status": "reference_baseline", "passes": None, "reason": "Reference policy; not compared to itself."}
    if policy == "oracle_upper_bound":
        return {"status": "upper_bound_not_deployable", "passes": None, "reason": "Oracle uses known simulator truth and cannot be deployed."}
    n = len(policy_rows)
    n_base = len(baseline_rows)
    replies = sum(r["outcome"]["reply"] for r in policy_rows)
    base_replies = sum(r["outcome"]["reply"] for r in baseline_rows)
    reply_diff = replies / n - base_replies / n_base if n and n_base else 0.0
    reply_ci_excludes_zero = bool(reply_difference_ci and reply_difference_ci[0] > 0.0)
    practical_lift_passes = reply_diff >= min_practical_lift
    safety = {}
    safety_failed = False
    for outcome_name, threshold in (("bounce", max_safety_excess), ("unsubscribe", max_safety_excess)):
        x = sum(r["outcome"][outcome_name] for r in policy_rows)
        xb = sum(r["outcome"][outcome_name] for r in baseline_rows)
        estimate = newcombe_difference_interval(wilson_interval(x, n), wilson_interval(xb, n_base))
        excess_upper = estimate.ci_upper
        safety[outcome_name] = {"difference": estimate.diff, "ci_lower": estimate.ci_lower,
                                "ci_upper": estimate.ci_upper, "max_allowed_excess": threshold}
        if estimate.diff > threshold or excess_upper > threshold:
            safety_failed = True
    passes = reply_ci_excludes_zero and practical_lift_passes and not safety_failed
    if safety_failed:
        reason = "Held-out safety uncertainty does not clear the documented bounce/unsubscribe guardrail."
        status = "safety_guardrail_failed"
    elif not reply_ci_excludes_zero:
        reason = "Paired bootstrap 95% interval does not exclude zero in the candidate policy's favor."
        status = "no_clear_measured_lift"
    elif not practical_lift_passes:
        reason = f"Observed reply-rate lift is below the {min_practical_lift:.1%} practical threshold."
        status = "practical_lift_not_met"
    else:
        reason = "Held-out reply lift, paired interval, practical threshold, and safety guardrails passed in this synthetic scenario."
        status = "passes_synthetic_heldout_gate"
    return {"status": status, "passes": passes, "reason": reason,
            "reply_difference": round(reply_diff, 6), "reply_difference_bootstrap_95_ci": reply_difference_ci,
            "min_practical_lift": min_practical_lift, "reply_ci_excludes_zero_in_favor": reply_ci_excludes_zero,
            "safety_guardrails": safety}


def evaluate_scenario(
    seed: int = DEFAULT_SEED,
    train_size: int = 3000,
    evaluation_size: int = 2000,
    equal_effect: bool = False,
) -> dict[str, Any]:
    """Train on a separate cohort and score every policy on the same evaluation population."""
    if train_size < 100 or evaluation_size < 100:
        raise ValueError("train_size and evaluation_size must each be >= 100")
    # Split is generated before policy fitting. Training labels never use evaluation outcomes.
    train = generate_population(train_size, seed * 17 + 1, equal_effect=equal_effect, id_prefix="train")
    heldout = generate_population(evaluation_size, seed * 17 + 2, equal_effect=equal_effect, id_prefix="heldout")
    learned = _learn(train, seed * 17 + 3)
    truth_best = {"technical": "A", "operations": "B"}
    if equal_effect:
        truth_best = {"technical": "A", "operations": "A"}  # tied upper bound; not a learned label

    policies = ("randomized_baseline_v1", "global_best_v1", "segment_best_v1", "oracle_upper_bound")
    assignments: dict[str, list[dict[str, Any]]] = {}
    for offset, policy in enumerate(policies):
        rng = random.Random(seed * 31 + offset + (100000 if equal_effect else 0))
        outcomes = []
        for lead in heldout:
            action = _choose(policy, lead["segment"], learned, truth_best, rng)
            outcomes.append({"lead_id": lead["lead_id"], "segment": lead["segment"], "action": action,
                             "outcome": lead["potential"][action]})
        assignments[policy] = outcomes

    baseline = assignments["randomized_baseline_v1"]
    baseline_rate = sum(r["outcome"]["reply"] for r in baseline) / len(baseline)
    results = {}
    for policy in policies:
        rows = assignments[policy]
        replies = sum(r["outcome"]["reply"] for r in rows)
        meetings = sum(r["outcome"]["meeting"] for r in rows)
        wins = sum(r["outcome"]["won"] for r in rows)
        bounces = sum(r["outcome"]["bounce"] for r in rows)
        unsubs = sum(r["outcome"]["unsubscribe"] for r in rows)
        rate = replies / len(rows)
        differences = [rows[i]["outcome"]["reply"] - baseline[i]["outcome"]["reply"] for i in range(len(rows))]
        ci = _bootstrap_difference(differences, seed * 97 + len(policy)) if policy != "randomized_baseline_v1" else [0.0, 0.0]
        alloc = {action: sum(1 for r in rows if r["action"] == action) for action in ("A", "B")}
        results[policy] = {
            "sample_size": len(rows), "action_allocation": alloc,
            "reply_count": replies, "reply_rate": round(rate, 6),
            "meeting_rate": round(meetings / len(rows), 6), "won_rate": round(wins / len(rows), 6),
            "bounce_rate": round(bounces / len(rows), 6), "unsubscribe_rate": round(unsubs / len(rows), 6),
            "difference_vs_baseline": round(rate - baseline_rate, 6),
            "difference_vs_baseline_bootstrap_95_ci": ci,
            "regret_vs_oracle": round(results_oracle_rate(assignments) - rate, 6),
            "heldout_evidence_gate": _heldout_evidence_gate(policy, rows, baseline, ci),
            "is_deployable": policy != "oracle_upper_bound",
        }
    return {
        "seed": seed, "equal_effect_negative_control": equal_effect,
        "training_size": train_size, "evaluation_size": evaluation_size,
        "learned_policy": learned, "policies": results,
        "ground_truth_assumptions": "Synthetic only: variant A/direct favors technical leads and variant B/curiosity favors operations leads." if not equal_effect else "Synthetic negative control: both variants have equal outcome probability within each segment.",
        "heldout_population_shared": True,
        "train_and_evaluation_disjoint": True,
        "limitations": ["All outcomes are synthetic potential outcomes.", "The oracle uses known simulator truth and is not a deployable policy.", "Bootstrap intervals describe this synthetic held-out cohort and are not product or causal evidence."],
    }


def results_oracle_rate(assignments: dict[str, list[dict[str, Any]]]) -> float:
    rows = assignments["oracle_upper_bound"]
    return sum(r["outcome"]["reply"] for r in rows) / len(rows) if rows else 0.0
