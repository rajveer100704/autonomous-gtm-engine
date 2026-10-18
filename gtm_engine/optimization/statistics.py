"""
Pure statistical routines for outcome feedback, confidence intervals,
and conservative promotion gating.

No heavy dependencies (standard library math only). Fully deterministic.
"""
from __future__ import annotations

import math
from typing import NamedTuple

# Standard normal critical value for two-sided 95% confidence
Z_95 = 1.959963984540054


class ProportionEstimate(NamedTuple):
    count: int
    total: int
    rate: float
    ci_lower: float
    ci_upper: float


class DifferenceEstimate(NamedTuple):
    diff: float
    ci_lower: float
    ci_upper: float
    excludes_zero: bool
    favors: str | None  # "A", "B", or None


class GateDecision(NamedTuple):
    passed: bool
    winner: str | None
    status: str  # "recommendation", "insufficient_evidence", "no_clear_winner", "safety_guardrail_failed"
    reason: str
    primary_metric: str
    diff_estimate: DifferenceEstimate | None
    safety_details: dict


def wilson_interval(count: int, total: int, z: float = Z_95) -> ProportionEstimate:
    """
    Computes Wilson score confidence interval for a binomial proportion.
    Handles n=0, x=0, x=n gracefully.
    """
    if total <= 0:
        # No denominator means the rate is unknown, not known to be zero.
        return ProportionEstimate(count=0, total=0, rate=0.0, ci_lower=0.0, ci_upper=1.0)

    count = max(0, min(count, total))
    p_hat = count / total

    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p_hat + z2 / (2.0 * total)) / denom
    spread = (z / denom) * math.sqrt((p_hat * (1.0 - p_hat) / total) + (z2 / (4.0 * total * total)))

    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)

    return ProportionEstimate(
        count=count,
        total=total,
        rate=round(p_hat, 6),
        ci_lower=round(lower, 6),
        ci_upper=round(upper, 6),
    )


def newcombe_difference_interval(
    prop_a: ProportionEstimate,
    prop_b: ProportionEstimate,
) -> DifferenceEstimate:
    """
    Computes Newcombe's hybrid score interval (Method 10) for the difference
    between two independent binomial proportions (p_A - p_B).

    Given Wilson intervals (l_A, u_A) and (l_B, u_B):
      L = (p_A - p_B) - sqrt((p_A - l_A)^2 + (u_B - p_B)^2)
      U = (p_A - p_B) + sqrt((u_A - p_A)^2 + (p_B - l_B)^2)
    """
    diff = prop_a.rate - prop_b.rate

    if prop_a.total <= 0 or prop_b.total <= 0:
        # With a missing denominator the effect is unidentified; retain full uncertainty.
        return DifferenceEstimate(
            diff=round(diff, 6), ci_lower=-1.0, ci_upper=1.0,
            excludes_zero=False, favors=None,
        )

    delta_l = math.sqrt((prop_a.rate - prop_a.ci_lower) ** 2 + (prop_b.ci_upper - prop_b.rate) ** 2)
    delta_u = math.sqrt((prop_a.ci_upper - prop_a.rate) ** 2 + (prop_b.rate - prop_b.ci_lower) ** 2)

    lower = max(-1.0, diff - delta_l)
    upper = min(1.0, diff + delta_u)

    excludes_zero = False
    favors = None

    if lower > 0.0:
        excludes_zero = True
        favors = "A"
    elif upper < 0.0:
        excludes_zero = True
        favors = "B"

    return DifferenceEstimate(
        diff=round(diff, 6),
        ci_lower=round(lower, 6),
        ci_upper=round(upper, 6),
        excludes_zero=excludes_zero,
        favors=favors,
    )


def evaluate_promotion_gate(
    stats_a: dict,
    stats_b: dict,
    min_sample_per_variant: int = 30,
    min_practical_lift: float = 0.02,
    max_safety_risk_excess: float = 0.03,
    z: float = Z_95,
) -> GateDecision:
    """
    Conservative evidence-backed gate for variant promotion:
    1. Sample adequacy: both variants must have >= min_sample_per_variant mature sent exposures.
    2. Primary metric (reply_rate): 95% Wilson difference interval must strictly exclude 0.
    3. Practical lift: point difference |diff| >= min_practical_lift.
    4. Safety guardrails: unsubscribe and bounce rates for proposed winner must not exceed
       runner-up plus safety threshold (or breach excessive risk bounds).

    stats_a and stats_b format:
    {
        "sent": int,
        "replied": int,
        "meetings": int,
        "won": int,
        "bounced": int,
        "unsubscribed": int,
    }
    """
    n_a = stats_a.get("sent", 0)
    n_b = stats_b.get("sent", 0)

    # 1. Sample adequacy check
    if n_a < min_sample_per_variant or n_b < min_sample_per_variant:
        under = []
        if n_a < min_sample_per_variant:
            under.append(f"A ({n_a}/{min_sample_per_variant})")
        if n_b < min_sample_per_variant:
            under.append(f"B ({n_b}/{min_sample_per_variant})")
        return GateDecision(
            passed=False,
            winner=None,
            status="insufficient_evidence",
            reason=f"Sample size below minimum requirement: {', '.join(under)} mature sent exposures",
            primary_metric="reply_rate",
            diff_estimate=None,
            safety_details={},
        )

    # 2. Wilson estimates for primary metric (reply_rate)
    prop_a = wilson_interval(stats_a.get("replied", 0), n_a, z=z)
    prop_b = wilson_interval(stats_b.get("replied", 0), n_b, z=z)
    diff_est = newcombe_difference_interval(prop_a, prop_b)

    # Check if statistically distinct from zero
    if not diff_est.excludes_zero or diff_est.favors is None:
        return GateDecision(
            passed=False,
            winner=None,
            status="no_clear_winner",
            reason=f"Difference interval [{diff_est.ci_lower:.4f}, {diff_est.ci_upper:.4f}] includes zero; no statistically significant advantage",
            primary_metric="reply_rate",
            diff_estimate=diff_est,
            safety_details={},
        )

    candidate_winner = diff_est.favors
    abs_diff = abs(diff_est.diff)

    # 3. Practical lift check
    if abs_diff < min_practical_lift:
        return GateDecision(
            passed=False,
            winner=None,
            status="no_clear_winner",
            reason=f"Observed difference ({abs_diff:.4f}) is below practical lift threshold ({min_practical_lift:.4f})",
            primary_metric="reply_rate",
            diff_estimate=diff_est,
            safety_details={},
        )

    # 4. Safety Guardrails (bounces & unsubscribes)
    unsub_a = wilson_interval(stats_a.get("unsubscribed", 0), n_a, z=z)
    unsub_b = wilson_interval(stats_b.get("unsubscribed", 0), n_b, z=z)
    bounce_a = wilson_interval(stats_a.get("bounced", 0), n_a, z=z)
    bounce_b = wilson_interval(stats_b.get("bounced", 0), n_b, z=z)

    safety_details = {
        "A": {
            "unsubscribed": unsub_a.rate,
            "unsub_ci": (unsub_a.ci_lower, unsub_a.ci_upper),
            "bounced": bounce_a.rate,
            "bounce_ci": (bounce_a.ci_lower, bounce_a.ci_upper),
        },
        "B": {
            "unsubscribed": unsub_b.rate,
            "unsub_ci": (unsub_b.ci_lower, unsub_b.ci_upper),
            "bounced": bounce_b.rate,
            "bounce_ci": (bounce_b.ci_lower, bounce_b.ci_upper),
        },
    }

    winner_unsub = unsub_a if candidate_winner == "A" else unsub_b
    loser_unsub = unsub_b if candidate_winner == "A" else unsub_a
    winner_bounce = bounce_a if candidate_winner == "A" else bounce_b
    loser_bounce = bounce_b if candidate_winner == "A" else bounce_a

    # If winner has materially worse safety rates than runner-up
    unsub_excess = winner_unsub.rate - loser_unsub.rate
    bounce_excess = winner_bounce.rate - loser_bounce.rate
    # Conservative uncertainty guard: a zero observed safety-event count is not proof of zero risk.
    unsub_upper_excess = winner_unsub.ci_upper - loser_unsub.ci_lower
    bounce_upper_excess = winner_bounce.ci_upper - loser_bounce.ci_lower
    safety_details["candidate_vs_competitor"] = {
        "unsubscribe_point_excess": round(unsub_excess, 6),
        "unsubscribe_upper_bound_excess": round(unsub_upper_excess, 6),
        "bounce_point_excess": round(bounce_excess, 6),
        "bounce_upper_bound_excess": round(bounce_upper_excess, 6),
        "max_allowed_excess": max_safety_risk_excess,
        "uncertainty_included": True,
    }

    if unsub_excess > max_safety_risk_excess or unsub_upper_excess > max_safety_risk_excess:
        return GateDecision(
            passed=False,
            winner=None,
            status="safety_guardrail_failed",
            reason=(f"Candidate {candidate_winner} fails unsubscribe safety guardrail: observed excess={unsub_excess:.4f}, "
                    f"upper-bound excess={unsub_upper_excess:.4f}, allowed={max_safety_risk_excess:.4f}"),
            primary_metric="reply_rate",
            diff_estimate=diff_est,
            safety_details=safety_details,
        )

    if bounce_excess > max_safety_risk_excess or bounce_upper_excess > max_safety_risk_excess:
        return GateDecision(
            passed=False,
            winner=None,
            status="safety_guardrail_failed",
            reason=(f"Candidate {candidate_winner} fails bounce safety guardrail: observed excess={bounce_excess:.4f}, "
                    f"upper-bound excess={bounce_upper_excess:.4f}, allowed={max_safety_risk_excess:.4f}"),
            primary_metric="reply_rate",
            diff_estimate=diff_est,
            safety_details=safety_details,
        )

    return GateDecision(
        passed=True,
        winner=candidate_winner,
        status="recommendation",
        reason=f"Variant {candidate_winner} passed statistical and safety gates with {abs_diff:.2%} lift (95% CI [{diff_est.ci_lower:.4f}, {diff_est.ci_upper:.4f}])",
        primary_metric="reply_rate",
        diff_estimate=diff_est,
        safety_details=safety_details,
    )
