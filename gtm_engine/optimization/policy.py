"""
Policy definitions for assignment, offline evaluation, and segment routing.

Policies:
  - randomized_baseline_v1: unbiased 50/50 exploration reference policy.
  - global_best_v1: selects globally promoted winner when evidence gates are met.
  - segment_best_v1: segment-aware routing when segment-level evidence clears gates,
                    falling back to global or randomized exploration.
"""
from __future__ import annotations

import random
from typing import Any, NamedTuple


VARIANT_STYLES = {
    "A": "direct",
    "B": "curiosity",
}

DEFAULT_POLICY_NAME = "randomized_baseline_v1"
POLICY_VERSION = "1.0"
EXPLOIT_PROBABILITY = 0.8
EXPLORE_PROBABILITY = 0.2


class PolicyAssignment(NamedTuple):
    variant: str
    action_value: str
    propensity: float
    policy_name: str
    policy_version: str
    segment_key: str
    is_fallback: bool
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "action_value": self.action_value,
            "propensity": round(self.propensity, 4),
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "segment_key": self.segment_key,
            "is_fallback": self.is_fallback,
            "context": self.context,
        }


def normalize_segment_key(lead: dict | None) -> str:
    """
    Extracts a controlled, low-cardinality segment key from lead metadata.
    Avoids high-cardinality explosion from arbitrary free-form strings.
    """
    if not lead:
        return "all"

    # 1. Check title/persona
    title = (lead.get("title") or "").strip().lower()
    if any(k in title for k in ("founder", "ceo", "co-founder", "cto", "cpo", "president", "owner")):
        return "tech_founder"
    if any(k in title for k in ("sales", "growth", "revenue", "cro", "bizdev", "marketing")):
        return "sales_leader"
    if any(k in title for k in ("operations", "coo", "ops", "finance", "cfo", "procurement")):
        return "ops_leader"

    # 2. Check industry in raw apollo data if present
    raw = lead.get("raw") or lead.get("raw_apollo_json") or {}
    if isinstance(raw, dict):
        ind = (raw.get("industry") or "").strip().lower()
        if "tech" in ind or "software" in ind or "saas" in ind:
            return "tech"
        if "finance" in ind or "bank" in ind or "insurance" in ind:
            return "finance"
        if "health" in ind or "medical" in ind or "pharma" in ind:
            return "healthcare"

    return "all"


class RandomizedBaselinePolicy:
    name = "randomized_baseline_v1"
    version = POLICY_VERSION

    @staticmethod
    def assign(segment_key: str = "all", context: dict | None = None) -> PolicyAssignment:
        variant = random.choice(["A", "B"])
        return PolicyAssignment(
            variant=variant,
            action_value=VARIANT_STYLES[variant],
            propensity=0.5,
            policy_name=RandomizedBaselinePolicy.name,
            policy_version=RandomizedBaselinePolicy.version,
            segment_key=segment_key,
            is_fallback=False,
            context=context or {},
        )


class GlobalBestPolicy:
    name = "global_best_v1"
    version = POLICY_VERSION

    @staticmethod
    def assign(
        active_winner: str | None = None,
        segment_key: str = "all",
        context: dict | None = None,
    ) -> PolicyAssignment:
        if active_winner in ("A", "B"):
            other = "B" if active_winner == "A" else "A"
            if random.random() < EXPLOIT_PROBABILITY:
                chosen = active_winner
                p = EXPLOIT_PROBABILITY
            else:
                chosen = other
                p = EXPLORE_PROBABILITY

            return PolicyAssignment(
                variant=chosen,
                action_value=VARIANT_STYLES[chosen],
                propensity=p,
                policy_name=GlobalBestPolicy.name,
                policy_version=GlobalBestPolicy.version,
                segment_key=segment_key,
                is_fallback=False,
                context=context or {},
            )

        # Fallback to 50/50 if no validated winner exists
        assignment = RandomizedBaselinePolicy.assign(segment_key=segment_key, context=context)
        return PolicyAssignment(
            variant=assignment.variant,
            action_value=assignment.action_value,
            propensity=assignment.propensity,
            policy_name=GlobalBestPolicy.name,
            policy_version=GlobalBestPolicy.version,
            segment_key=segment_key,
            is_fallback=True,
            context=context or {},
        )


class SegmentBestPolicy:
    name = "segment_best_v1"
    version = POLICY_VERSION

    @staticmethod
    def assign(
        segment_winners: dict[str, str],
        active_global_winner: str | None = None,
        segment_key: str = "all",
        context: dict | None = None,
    ) -> PolicyAssignment:
        # Check segment-specific winner
        seg_win = segment_winners.get(segment_key)
        if seg_win in ("A", "B"):
            other = "B" if seg_win == "A" else "A"
            if random.random() < EXPLOIT_PROBABILITY:
                chosen = seg_win
                p = EXPLOIT_PROBABILITY
            else:
                chosen = other
                p = EXPLORE_PROBABILITY

            return PolicyAssignment(
                variant=chosen,
                action_value=VARIANT_STYLES[chosen],
                propensity=p,
                policy_name=SegmentBestPolicy.name,
                policy_version=SegmentBestPolicy.version,
                segment_key=segment_key,
                is_fallback=False,
                context=context or {},
            )

        # Fallback to global best policy
        global_asg = GlobalBestPolicy.assign(
            active_winner=active_global_winner,
            segment_key=segment_key,
            context=context,
        )
        return PolicyAssignment(
            variant=global_asg.variant,
            action_value=global_asg.action_value,
            propensity=global_asg.propensity,
            policy_name=SegmentBestPolicy.name,
            policy_version=SegmentBestPolicy.version,
            segment_key=segment_key,
            is_fallback=True,
            context=context or {},
        )
