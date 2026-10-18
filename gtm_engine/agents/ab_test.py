"""
A/B testing for first-touch email copy.

V5 Closed-Loop Feedback Integration:
1. assign_variant_with_probability() — selects variant and records exact assignment propensity
   (50/50 randomized exploration by default, or 80/20 explore/exploit split if a validated
   winner has been promoted).
2. assign_variant() — backwards-compatible signature returning a string variant ('A' or 'B').
3. evaluate_and_promote() — evaluates mature, explicitly attributed outcomes using conservative
   statistical (Wilson difference) and safety guardrail gates. Idempotent.
"""
from __future__ import annotations

import datetime
from typing import Any

from gtm_engine.crm import db
from gtm_engine.optimization.policy import (
    GlobalBestPolicy,
    VARIANT_STYLES,
    POLICY_VERSION,
    EXPLOIT_PROBABILITY,
    EXPLORE_PROBABILITY,
)
from gtm_engine.optimization.feedback import OutcomeFeedbackService

EXPLOIT_WINNER_PROBABILITY = EXPLOIT_PROBABILITY
MIN_SAMPLE_PER_VARIANT = 30         # Conservative minimum hygiene sample (configurable)
MIN_LEAD_MARGIN = 1.2              # Backwards-compatible margin alias
MIN_PRACTICAL_LIFT = 0.02           # 2 percentage points minimum lift
ATTRIBUTION_WINDOW_DAYS = 14        # Mature attribution window in days
DECISION_METRIC = "reply_rate"     # Optimization objective


def assign_variant_with_probability(
    test_name: str = "email_copy_v1",
    segment_key: str = "all",
    context: dict | None = None,
) -> dict[str, Any]:
    """
    Selects variant and returns full policy assignment metadata including exact
    assignment probability (propensity).
    """
    winner_row = db.get_validated_active_winner(test_name)
    winner = winner_row["variant"] if winner_row else None
    assignment = GlobalBestPolicy.assign(
        active_winner=winner,
        segment_key=segment_key,
        context=context,
    )
    return assignment.to_dict()


def assign_variant(test_name: str = "email_copy_v1") -> str:
    """
    Backwards-compatible variant assignment returning 'A' or 'B'.
    """
    res = assign_variant_with_probability(test_name=test_name)
    return res["variant"]


def evaluate_and_promote(
    test_name: str = "email_copy_v1",
    min_sample: int | None = None,
    min_lift: float | None = None,
    attribution_window_days: int | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """
    Evaluates strategy performance on mature, explicitly attributed outcomes.
    Promotes winner to ab_winner only if statistical confidence and safety gates pass.
    Idempotent: does not append duplicate winner rows on repeated evaluations.
    """
    sample_req = min_sample if min_sample is not None else MIN_SAMPLE_PER_VARIANT
    lift_req = min_lift if min_lift is not None else MIN_PRACTICAL_LIFT
    window = attribution_window_days if attribution_window_days is not None else ATTRIBUTION_WINDOW_DAYS

    return OutcomeFeedbackService.evaluate_and_promote(
        test_name=test_name,
        min_sample_per_variant=sample_req,
        min_practical_lift=lift_req,
        attribution_window_days=window,
        now=now,
    )
