"""
Outcome Feedback Service.

Connects mature, explicitly attributed outcomes to historical decisions,
evaluates strategy performance with Wilson confidence bounds,
performs conservative promotion gating, and generates evidence-backed recommendations.
"""
from __future__ import annotations

import datetime
from typing import Any

from gtm_engine.crm import db
from gtm_engine.optimization.statistics import (
    wilson_interval,
    newcombe_difference_interval,
    evaluate_promotion_gate,
    ProportionEstimate,
    DifferenceEstimate,
    GateDecision,
)
from gtm_engine.optimization.policy import VARIANT_STYLES, POLICY_VERSION


class OutcomeFeedbackService:
    @staticmethod
    def get_dataset_summary(
        attribution_window_days: int = 14,
        now: datetime.datetime | None = None,
        include_simulated: bool = False,
    ) -> dict[str, Any]:
        """
        Gathers explicitly attributed outcomes, calculates rates and 95% Wilson intervals,
        and aggregates by variant and segment.
        """
        raw_data = db.get_attribution_dataset(
            attribution_window_days=attribution_window_days,
            now=now,
            include_simulated=include_simulated,
        )

        variant_stats = raw_data["variant_stats"]
        processed_variants = {}

        for var in ("A", "B"):
            stats = variant_stats.get(var, {
                "eligible_sent": 0, "mature_sent": 0, "immature_sent": 0,
                "replied": 0, "meetings": 0, "won": 0, "bounced": 0, "unsubscribed": 0, "opened": 0,
            })
            denom = stats["mature_sent"]

            reply_est = wilson_interval(stats["replied"], denom)
            meeting_est = wilson_interval(stats["meetings"], denom)
            won_est = wilson_interval(stats["won"], denom)
            bounce_est = wilson_interval(stats["bounced"], denom)
            unsub_est = wilson_interval(stats["unsubscribed"], denom)
            open_est = wilson_interval(stats["opened"], denom)

            processed_variants[var] = {
                "action_value": VARIANT_STYLES.get(var, "unknown"),
                "eligible_sent": stats["eligible_sent"],
                "mature_sent": denom,
                "immature_sent": stats["immature_sent"],
                "counts": {
                    "replied": stats["replied"],
                    "meetings": stats["meetings"],
                    "won": stats["won"],
                    "bounced": stats["bounced"],
                    "unsubscribed": stats["unsubscribed"],
                    "opened": stats["opened"],
                },
                "rates": {
                    "reply_rate": reply_est.rate,
                    "reply_ci": [reply_est.ci_lower, reply_est.ci_upper],
                    "meeting_rate": meeting_est.rate,
                    "meeting_ci": [meeting_est.ci_lower, meeting_est.ci_upper],
                    "won_rate": won_est.rate,
                    "won_ci": [won_est.ci_lower, won_est.ci_upper],
                    "bounce_rate": bounce_est.rate,
                    "bounce_ci": [bounce_est.ci_lower, bounce_est.ci_upper],
                    "unsubscribe_rate": unsub_est.rate,
                    "unsubscribe_ci": [unsub_est.ci_lower, unsub_est.ci_upper],
                    "open_rate": open_est.rate,
                    "open_ci": [open_est.ci_lower, open_est.ci_upper],
                },
            }

        # Difference between A and B on mature replies
        prop_a = wilson_interval(
            variant_stats.get("A", {}).get("replied", 0),
            variant_stats.get("A", {}).get("mature_sent", 0),
        )
        prop_b = wilson_interval(
            variant_stats.get("B", {}).get("replied", 0),
            variant_stats.get("B", {}).get("mature_sent", 0),
        )
        diff_est = newcombe_difference_interval(prop_a, prop_b)

        # Process segments
        processed_segments = {}
        for seg_key, seg_data in raw_data["segment_stats"].items():
            seg_variants = {}
            for var in ("A", "B"):
                sv_stats = seg_data.get(var, {
                    "eligible_sent": 0, "mature_sent": 0, "immature_sent": 0,
                    "replied": 0, "meetings": 0, "won": 0, "bounced": 0, "unsubscribed": 0,
                })
                s_denom = sv_stats["mature_sent"]
                s_rep = wilson_interval(sv_stats["replied"], s_denom)
                s_meet = wilson_interval(sv_stats.get("meetings", 0), s_denom)
                s_won = wilson_interval(sv_stats.get("won", 0), s_denom)
                s_lost = wilson_interval(sv_stats.get("lost", 0), s_denom)
                s_unsub = wilson_interval(sv_stats["unsubscribed"], s_denom)
                s_bounce = wilson_interval(sv_stats["bounced"], s_denom)

                seg_variants[var] = {
                    "mature_sent": s_denom,
                    "counts": {"replied": sv_stats["replied"], "meetings": sv_stats.get("meetings", 0),
                               "won": sv_stats.get("won", 0), "lost": sv_stats.get("lost", 0),
                               "bounced": sv_stats["bounced"], "unsubscribed": sv_stats["unsubscribed"]},
                    "reply_rate": s_rep.rate,
                    "reply_ci": [s_rep.ci_lower, s_rep.ci_upper],
                    "meeting_rate": s_meet.rate,
                    "meeting_ci": [s_meet.ci_lower, s_meet.ci_upper],
                    "won_rate": s_won.rate,
                    "won_ci": [s_won.ci_lower, s_won.ci_upper],
                    "lost_rate": s_lost.rate,
                    "lost_ci": [s_lost.ci_lower, s_lost.ci_upper],
                    "unsubscribed": sv_stats["unsubscribed"],
                    "unsubscribe_rate": s_unsub.rate,
                    "unsubscribe_ci": [s_unsub.ci_lower, s_unsub.ci_upper],
                    "bounced": sv_stats["bounced"],
                    "bounce_rate": s_bounce.rate,
                    "bounce_ci": [s_bounce.ci_lower, s_bounce.ci_upper],
                }
            processed_segments[seg_key] = seg_variants

        return {
            "provenance": "synthetic_simulation" if include_simulated else "real_attributed",
            "as_of_time": raw_data["as_of_time"],
            "attribution_window_days": raw_data["attribution_window_days"],
            "cohort": {
                "total_decisions": raw_data["total_decisions"],
                "sent_exposures": raw_data["sent_exposures"],
                "mature_exposures": raw_data["mature_exposures"],
                "immature_exposures": raw_data["immature_exposures"],
                "excluded_simulated_exposures": raw_data["excluded_simulated_exposures"],
                "excluded_real_exposures": raw_data["excluded_real_exposures"],
                "attributed_outcomes_count": raw_data["attributed_outcomes_count"],
                "unattributed_events_count": raw_data["unattributed_events_count"],
                "excluded_outcomes_invalid_or_outside_window": raw_data["excluded_outcomes_invalid_or_outside_window"],
            },
            "variants": processed_variants,
            "difference_a_minus_b": {
                "metric": "reply_rate",
                "diff": diff_est.diff,
                "ci_lower": diff_est.ci_lower,
                "ci_upper": diff_est.ci_upper,
                "excludes_zero": diff_est.excludes_zero,
                "favors": diff_est.favors,
            },
            "segments": processed_segments,
            "data_quality_warnings": raw_data["data_quality_warnings"],
        }

    @staticmethod
    def get_recommendation(
        segment_key: str = "all",
        attribution_window_days: int = 14,
        now: datetime.datetime | None = None,
        min_sample_per_variant: int = 30,
        min_practical_lift: float = 0.02,
        max_safety_risk_excess: float = 0.03,
        include_simulated: bool = False,
    ) -> dict[str, Any]:
        """
        Generates an advisory recommendation based strictly on mature,
        explicitly attributed data and statistical/safety gates.
        Read-only. Never mutates database or changes campaign traffic.
        """
        summary = OutcomeFeedbackService.get_dataset_summary(
            attribution_window_days=attribution_window_days,
            now=now,
            include_simulated=include_simulated,
        )

        caveats = [
            f"Advisory recommendation only. No live traffic allocation was altered.",
            f"Requires at least {min_sample_per_variant} mature sent exposures per variant with {attribution_window_days}-day attribution window.",
            f"Primary metric: mature email_replied rate with 95% Newcombe/Wilson difference interval.",
        ]

        if summary["cohort"]["immature_exposures"] > 0:
            caveats.append(
                f"{summary['cohort']['immature_exposures']} exposures have not yet reached the {attribution_window_days}-day maturity threshold and were excluded."
            )
        if summary["cohort"]["unattributed_events_count"] > 0:
            caveats.append(
                f"{summary['cohort']['unattributed_events_count']} events in the CRM lack verifiable outreach links and were excluded from strategy evaluation."
            )

        fallback_used = False
        target_stats_a = None
        target_stats_b = None
        eval_scope = "global"

        # Check segment-specific data if requested
        if segment_key and segment_key != "all":
            seg_dict = summary["segments"].get(segment_key)
            if seg_dict:
                sa = seg_dict.get("A", {})
                sb = seg_dict.get("B", {})
                na = sa.get("mature_sent", 0)
                nb = sb.get("mature_sent", 0)

                if na >= min_sample_per_variant and nb >= min_sample_per_variant:
                    target_stats_a = {
                        "sent": na,
                        "replied": sa.get("replied", 0),
                        "bounced": sa.get("bounced", 0),
                        "unsubscribed": sa.get("unsubscribed", 0),
                    }
                    target_stats_b = {
                        "sent": nb,
                        "replied": sb.get("replied", 0),
                        "bounced": sb.get("bounced", 0),
                        "unsubscribed": sb.get("unsubscribed", 0),
                    }
                    eval_scope = f"segment:{segment_key}"
                else:
                    fallback_used = True
                    caveats.append(
                        f"Segment '{segment_key}' has inadequate sample size (A={na}, B={nb} vs req {min_sample_per_variant}). Falling back to global cohort."
                    )
            else:
                fallback_used = True
                caveats.append(f"Segment '{segment_key}' has no recorded history. Falling back to global cohort.")

        # Fallback or global evaluation
        if target_stats_a is None or target_stats_b is None:
            va = summary["variants"]["A"]
            vb = summary["variants"]["B"]
            target_stats_a = {
                "sent": va["mature_sent"],
                "replied": va["counts"]["replied"],
                "bounced": va["counts"]["bounced"],
                "unsubscribed": va["counts"]["unsubscribed"],
            }
            target_stats_b = {
                "sent": vb["mature_sent"],
                "replied": vb["counts"]["replied"],
                "bounced": vb["counts"]["bounced"],
                "unsubscribed": vb["counts"]["unsubscribed"],
            }

        gate_decision = evaluate_promotion_gate(
            target_stats_a,
            target_stats_b,
            min_sample_per_variant=min_sample_per_variant,
            min_practical_lift=min_practical_lift,
            max_safety_risk_excess=max_safety_risk_excess,
        )

        n_a = target_stats_a["sent"]
        n_b = target_stats_b["sent"]
        rate_a = target_stats_a["replied"] / n_a if n_a > 0 else 0.0
        rate_b = target_stats_b["replied"] / n_b if n_b > 0 else 0.0

        diff_info = {}
        if gate_decision.diff_estimate:
            de = gate_decision.diff_estimate
            diff_info = {
                "difference": de.diff,
                "ci_lower": de.ci_lower,
                "ci_upper": de.ci_upper,
                "excludes_zero": de.excludes_zero,
                "favors": de.favors,
            }

        recommended_action = gate_decision.winner if gate_decision.passed else None
        recommended_style = VARIANT_STYLES.get(recommended_action) if recommended_action else None

        policy_name = "randomized_baseline_v1"
        if gate_decision.passed:
            policy_name = "segment_best_v1" if eval_scope.startswith("segment:") else "global_best_v1"

        return {
            "status": gate_decision.status,
            "recommended_action": recommended_action,
            "recommended_style": recommended_style,
            "policy_name": policy_name,
            "policy_version": POLICY_VERSION,
            "evaluation_scope": eval_scope,
            "fallback_used": fallback_used,
            "sample_sizes": {
                "A": n_a,
                "B": n_b,
                "total_mature": n_a + n_b,
                "min_sample_required": min_sample_per_variant,
            },
            "estimated_rates": {
                "A": round(rate_a, 4),
                "B": round(rate_b, 4),
            },
            "difference_interval": diff_info,
            "rationale": gate_decision.reason,
            "safety_guardrails": gate_decision.safety_details,
            "caveats": caveats,
        }

    @staticmethod
    def evaluate_and_promote(
        test_name: str = "email_copy_v1",
        min_sample_per_variant: int = 30,
        min_practical_lift: float = 0.02,
        attribution_window_days: int = 14,
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        """
        Safe evidence-backed evaluation and idempotent promotion.
        Replaces the old raw reply-rate ratio with the statistical/safety gate.
        Preserves backwards-compatible response fields:
          decided, winner, metric, value, margin, performance, reason
        """
        rec = OutcomeFeedbackService.get_recommendation(
            segment_key="all",
            attribution_window_days=attribution_window_days,
            min_sample_per_variant=min_sample_per_variant,
            min_practical_lift=min_practical_lift,
            now=now,
        )

        summary = OutcomeFeedbackService.get_dataset_summary(
            attribution_window_days=attribution_window_days,
            now=now,
        )
        perf_legacy = {}
        for var in ("A", "B"):
            vdata = summary["variants"][var]
            perf_legacy[var] = {
                "sent": vdata["mature_sent"],
                "opened": vdata["counts"]["opened"],
                "replied": vdata["counts"]["replied"],
                "meetings": vdata["counts"]["meetings"],
                "won": vdata["counts"]["won"],
                "bounced": vdata["counts"]["bounced"],
                "unsubscribed": vdata["counts"]["unsubscribed"],
                "open_rate": vdata["rates"]["open_rate"],
                "reply_rate": vdata["rates"]["reply_rate"],
                "meeting_rate": vdata["rates"]["meeting_rate"],
                "reply_ci": vdata["rates"]["reply_ci"],
            }

        if rec["status"] == "recommendation" and rec["recommended_action"]:
            winner = rec["recommended_action"]
            winner_val = rec["estimated_rates"][winner]
            loser = "B" if winner == "A" else "A"
            loser_val = rec["estimated_rates"][loser]

            margin = round(winner_val / loser_val, 2) if loser_val > 0 else None

            # Only a validated V5 promotion may influence future assignment. The legacy
            # ab_winner row is retained for API/UI backward compatibility.
            import hashlib, json
            evidence_payload = {
                "test_name": test_name, "winner": winner, "policy_version": POLICY_VERSION,
                "n_a": rec.get("sample_sizes", {}).get("A"),
                "n_b": rec.get("sample_sizes", {}).get("B"),
                "rates": rec.get("estimated_rates"), "interval": rec.get("difference_interval"),
                "window_days": attribution_window_days,
            }
            evidence_key = hashlib.sha256(json.dumps(evidence_payload, sort_keys=True, default=str).encode()).hexdigest()
            db.record_validated_promotion(test_name, winner, POLICY_VERSION, evidence_key)
            active_winner = db.get_active_winner(test_name)
            if not active_winner or active_winner.get("variant") != winner:
                db.set_winner(winner, "reply_rate", winner_val, test_name)

            return {
                "decided": True,
                "winner": winner,
                "metric": "reply_rate",
                "value": winner_val,
                "margin": margin,
                "performance": perf_legacy,
                "reason": rec["rationale"],
                "status": "recommendation",
                "evidence": rec,
            }
        else:
            return {
                "decided": False,
                "winner": None,
                "metric": "reply_rate",
                "value": 0.0,
                "margin": 1.0,
                "performance": perf_legacy,
                "reason": rec["rationale"],
                "status": rec["status"],
                "evidence": rec,
            }

    @staticmethod
    def off_policy_evaluation(
        records: list[dict],
        target_policy_name: str,
        target_policy_version: str = "1.0",
        min_effective_sample: float = 10.0,
        max_weight_clip: float = 20.0,
        target_action: str | None = None,
        target_segment_actions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Computes an off-policy causal evaluation using Self-Normalized Inverse
        Propensity Scoring (SNIPS) over logged decision records.
        Returns not_evaluable_for_causal_policy_comparison if propensity logging
        or common support is violated.
        """
        if not records:
            return {
                "status": "not_evaluable_for_causal_policy_comparison",
                "reason": "No logged decision records provided.",
            }
        if target_policy_name == "global_best_v1" and target_action not in ("A", "B"):
            return {"status": "not_evaluable_for_causal_policy_comparison",
                    "reason": "global_best_v1 requires an explicitly supplied target_action (A or B)."}
        if target_policy_name == "segment_best_v1" and not target_segment_actions:
            return {"status": "not_evaluable_for_causal_policy_comparison",
                    "reason": "segment_best_v1 requires explicit target_segment_actions."}
        if target_policy_name not in ("randomized_baseline_v1", "global_best_v1", "segment_best_v1"):
            return {"status": "not_evaluable_for_causal_policy_comparison",
                    "reason": f"Unsupported target policy {target_policy_name!r}."}

        weights = []
        outcomes = []

        for r in records:
            if r.get("propensity_valid") is False:
                return {
                    "status": "not_evaluable_for_causal_policy_comparison",
                    "reason": f"Record {r.get('decision_id')} has a recovered/invalid propensity and is excluded from causal policy comparison.",
                }
            p_logged = r.get("assignment_probability")
            if p_logged is None or p_logged <= 0.0 or p_logged > 1.0:
                return {
                    "status": "not_evaluable_for_causal_policy_comparison",
                    "reason": f"Record {r.get('decision_id')} lacks valid logged assignment propensity.",
                }

            # Target policy probability for the observed action
            observed_action = r.get("action")
            # In our setup, baseline targets 0.5; global exploit targets 0.8 / 0.2
            if target_policy_name == "randomized_baseline_v1":
                p_target = 0.5
            elif target_policy_name == "global_best_v1":
                p_target = 0.8 if observed_action == target_action else 0.2
            else:
                segment = r.get("segment_key")
                segment_action = (target_segment_actions or {}).get(segment)
                if segment_action not in ("A", "B"):
                    return {"status": "not_evaluable_for_causal_policy_comparison",
                            "reason": f"No valid target action supplied for segment {segment!r}."}
                p_target = 0.8 if observed_action == segment_action else 0.2

            w = p_target / p_logged
            w_clipped = min(w, max_weight_clip)
            weights.append(w_clipped)
            outcomes.append(r.get("replied", 0))

        sum_w = sum(weights)
        if sum_w <= 0.0:
            return {
                "status": "not_evaluable_for_causal_policy_comparison",
                "reason": "Sum of importance weights is zero.",
            }

        # Effective Sample Size (Kish's ESS = (sum w)^2 / sum(w^2))
        sum_w_sq = sum(w * w for w in weights)
        ess = (sum_w * sum_w) / sum_w_sq if sum_w_sq > 0 else 0.0

        if ess < min_effective_sample:
            return {
                "status": "not_evaluable_for_causal_policy_comparison",
                "reason": f"Effective sample size ({ess:.1f}) is below minimum requirement ({min_effective_sample}).",
            }

        # SNIPS estimate: sum(w_i * y_i) / sum(w_i)
        snips_est = sum(w * y for w, y in zip(weights, outcomes)) / sum_w

        return {
            "status": "evaluated",
            "target_policy": target_policy_name,
            "sample_size": len(records),
            "effective_sample_size": round(ess, 2),
            "snips_outcome_estimate": round(snips_est, 4),
            "weights_clipped_count": sum(1 for w in weights if w >= max_weight_clip),
        }
