"""
A/B testing for first-touch email copy. Two concerns, kept separate:

1. assign_variant() — which variant does THIS lead get? 50/50 explore until
   a winner is declared, then an explore/exploit split (80% winner, 20%
   the other) so we keep collecting signal on the loser instead of fully
   committing — a real GTM team wouldn't want a "winner" locked in forever
   off a small early sample.

2. evaluate_and_promote() — looks at crm.db.variant_performance() and
   decides whether one variant has pulled ahead with enough samples to
   act on. Call this periodically (e.g. from the scheduler, or a
   dashboard button) — it's cheap and idempotent.
"""
import random
from gtm_engine.crm import db

EXPLOIT_WINNER_PROBABILITY = 0.8
MIN_SAMPLE_PER_VARIANT = 10        # don't call a winner off a handful of sends
MIN_LEAD_MARGIN = 1.2              # winner must beat runner-up by >=20% on the metric
DECISION_METRIC = "reply_rate"     # what "better" means for this test


def assign_variant(test_name: str = "email_copy_v1") -> str:
    winner_row = db.get_active_winner(test_name)
    if not winner_row:
        return random.choice(["A", "B"])

    winner = winner_row["variant"]
    loser = "B" if winner == "A" else "A"
    return winner if random.random() < EXPLOIT_WINNER_PROBABILITY else loser


def evaluate_and_promote(test_name: str = "email_copy_v1") -> dict:
    performance = db.variant_performance()
    if len(performance) < 2:
        return {"decided": False, "reason": "need both variants represented", "performance": performance}

    under_sample = [v for v, stats in performance.items() if stats["sent"] < MIN_SAMPLE_PER_VARIANT]
    if under_sample:
        return {
            "decided": False,
            "reason": f"variant(s) {under_sample} below min sample size ({MIN_SAMPLE_PER_VARIANT})",
            "performance": performance,
        }

    ranked = sorted(performance.items(), key=lambda kv: kv[1][DECISION_METRIC], reverse=True)
    (top_variant, top_stats), (second_variant, second_stats) = ranked[0], ranked[1]

    if second_stats[DECISION_METRIC] == 0:
        margin = float("inf") if top_stats[DECISION_METRIC] > 0 else 1.0
    else:
        margin = top_stats[DECISION_METRIC] / second_stats[DECISION_METRIC]

    if margin < MIN_LEAD_MARGIN:
        return {
            "decided": False,
            "reason": f"lead ({margin:.2f}x) below required margin ({MIN_LEAD_MARGIN}x)",
            "performance": performance,
        }

    db.set_winner(top_variant, DECISION_METRIC, top_stats[DECISION_METRIC], test_name)
    return {
        "decided": True,
        "winner": top_variant,
        "metric": DECISION_METRIC,
        "value": top_stats[DECISION_METRIC],
        "margin": round(margin, 2),
        "performance": performance,
    }
