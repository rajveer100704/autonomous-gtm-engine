"""Run deterministic agent-task evaluations without external provider side effects.

Usage from the repository root::

    python -m gtm_engine.evals.agent_task_eval

The suite exercises existing GTM components with mocked LLM responses and an
isolated in-memory SQLite queue. It is a functional regression benchmark, not
an evaluation of live Gemini quality or real campaign outcomes.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import create_engine

from gtm_engine.config import settings


def _result(
    scenario_id: str,
    description: str,
    checks: dict[str, bool],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "description": description,
        "passed": all(checks.values()),
        "checks": checks,
        "evidence": evidence,
    }


def _scenario_campaign_planning() -> dict[str, Any]:
    from gtm_engine.agents.planner_agent import plan_campaign

    request = {
        "objective": "Book 3 demos with VP Sales at LegalTech SaaS companies",
        "max_leads": 3,
        "focus": "Series A LegalTech companies with active sales hiring",
    }
    plan = plan_campaign(**request)
    checks = {
        "objective_preserved": plan.get("objective") == request["objective"],
        "lead_limit_preserved": plan.get("max_leads") == 3,
        "email_channel_available": "email" in plan.get("channels", []),
        "icp_titles_present": bool(plan.get("icp", {}).get("titles")),
        "focus_preserved": plan.get("focus") == request["focus"],
    }
    return _result(
        "campaign_planning",
        "Campaign planner preserves the requested objective, lead cap, focus, ICP, and email channel.",
        checks,
        {"objective": plan.get("objective"), "max_leads": plan.get("max_leads"),
         "channels": plan.get("channels"), "focus": plan.get("focus")},
    )


def _scenario_goal_structure() -> dict[str, Any]:
    from gtm_engine.agents.goal_manager import goal_manager_node

    output = goal_manager_node({"objective": "Book a qualified demo with the VP Sales"})
    plan = output.get("plan", {})
    goal = plan.get("goal", {})
    constraints = goal.get("constraints", [])
    checks = {
        "plan_created": plan.get("status") == "active",
        "objective_preserved": goal.get("objective") == "Book a qualified demo with the VP Sales",
        "success_criteria_present": "meeting_booked" in goal.get("success_criteria", ""),
        "budget_positive": isinstance(goal.get("budget"), (int, float)) and goal["budget"] > 0,
        "constraints_present": len(constraints) >= 3,
        "deadline_present": bool(goal.get("deadline")),
    }
    return _result(
        "goal_structure",
        "Goal Manager creates an active goal with success criteria, constraints, budget, and deadline.",
        checks,
        {"status": plan.get("status"), "success_criteria": goal.get("success_criteria"),
         "constraints": constraints, "budget_usd": goal.get("budget"),
         "deadline": goal.get("deadline")},
    )


def _scenario_budget_routing() -> dict[str, Any]:
    from gtm_engine.agents.goal_manager import BudgetManager

    manager = BudgetManager(limit=0.10)
    under_budget = {
        "research": manager.select_model("research"),
        "evaluation": manager.select_model("evaluation"),
        "planning": manager.select_model("planning"),
    }
    manager.record_spend(0.11)
    exhausted_model = manager.select_model("planning")
    checks = {
        "research_uses_flash": under_budget["research"] == "gemini-2.5-flash",
        "evaluation_uses_flash": under_budget["evaluation"] == "gemini-2.5-flash",
        "planning_uses_pro_under_budget": under_budget["planning"] == "gemini-2.5-pro",
        "exhausted_budget_forces_flash": exhausted_model == "gemini-2.5-flash",
        "budget_exhaustion_detected": manager.is_exhausted(),
    }
    return _result(
        "budget_aware_model_policy",
        "BudgetManager selects task-tier models and falls back to Flash after the budget is exceeded.",
        checks,
        {"under_budget_routes": under_budget, "spend_usd": manager.spend,
         "limit_usd": manager.limit, "exhausted_route": exhausted_model},
    )


def _scenario_email_generation() -> dict[str, Any]:
    from gtm_engine.agents.email_agent import generate

    lead = {"first_name": "Maya", "company": "Acme Legal", "title": "Head of Sales"}
    email = generate(lead, ["contract and doc turnaround"], style="direct")
    combined = f"{email.get('subject', '')}\n{email.get('body', '')}"
    unresolved = [token for token in ("{first_name}", "{company}", "{pitch}", "{sender_name}")
                  if token in combined]
    checks = {
        "subject_nonempty": bool(email.get("subject", "").strip()),
        "body_nonempty": bool(email.get("body", "").strip()),
        "recipient_personalized": "Maya" in combined,
        "company_personalized": "Acme Legal" in combined,
        "no_known_placeholders_left": not unresolved,
    }
    return _result(
        "personalized_email_generation",
        "Email agent returns a populated draft with recipient/company context and resolved template placeholders.",
        checks,
        {"subject": email.get("subject"), "body_word_count": len(email.get("body", "").split()),
         "unresolved_placeholders": unresolved},
    )


def _scenario_email_quality_approval() -> dict[str, Any]:
    from gtm_engine.agents.evaluation_agent import evaluate

    lead = {"first_name": "Maya", "company": "Acme Legal", "title": "Head of Sales"}
    pain_point = "contract and doc turnaround"
    email = {
        "subject": "A question about contract workflows at Acme Legal",
        "body": (
            "Hi Maya, I have a note that contract and doc turnaround is a priority at "
            "Acme Legal. Our platform helps teams generate, review, and manage documents "
            "with AI. I would be glad to share a practical example focused on contract "
            "and doc turnaround. Would a 15-minute conversation next week be useful? "
            "Best, Alex"
        ),
    }
    evaluation = evaluate(email, lead, pain_points=[pain_point], threshold=7.0)
    checks = {
        "hard_checks_pass": evaluation["hard_checks"]["passed"],
        "score_meets_threshold": evaluation["combined_score"] >= evaluation["threshold"],
        "draft_approved": evaluation["approved"],
    }
    return _result(
        "email_quality_gate_approval",
        "Evaluation gate approves a grounded, personalized email that satisfies hard checks and score threshold.",
        checks,
        {"approved": evaluation["approved"], "combined_score": evaluation["combined_score"],
         "threshold": evaluation["threshold"],
         "hard_check_failures": evaluation["hard_checks"]["failures"]},
    )


def _scenario_spam_rejection() -> dict[str, Any]:
    from gtm_engine.agents.evaluation_agent import evaluate

    lead = {"first_name": "Maya", "company": "Acme Legal", "title": "Head of Sales"}
    spam_email = {
        "subject": "A question for Maya at Acme Legal",
        "body": (
            "Hi Maya, Acme Legal can act now for a limited time. This risk free offer "
            "will double your results. Click here to buy now and claim your guaranteed "
            "cash bonus. I would like to discuss this opportunity with you today. "
            "Best, Alex"
        ),
    }
    evaluation = evaluate(spam_email, lead, threshold=7.0)
    failures = evaluation["hard_checks"]["failures"]
    checks = {
        "spam_score_exceeds_limit": evaluation["hard_checks"]["spam_score"] > 6.0,
        "spam_failure_recorded": any(item.startswith("high_spam_score") for item in failures),
        "draft_rejected": evaluation["approved"] is False,
        "llm_review_skipped": evaluation["llm_review"]["feedback"] == "Skipped — hard checks failed.",
    }
    return _result(
        "unsafe_email_rejection",
        "Evaluation gate rejects a spam-trigger-heavy email at the deterministic hard-check stage.",
        checks,
        {"approved": evaluation["approved"], "spam_score": evaluation["hard_checks"]["spam_score"],
         "hard_check_failures": failures, "llm_review_feedback": evaluation["llm_review"]["feedback"]},
    )


def _scenario_unsubscribe_classification() -> dict[str, Any]:
    from gtm_engine.agents.reply_classifier_agent import classify

    result = classify("Please unsubscribe me and remove me from all future outreach.",
                      {"first_name": "Maya", "id": 101})
    checks = {
        "unsubscribe_detected": result.get("classification") == "unsubscribe",
        "confidence_returned": result.get("confidence") in {"low", "medium", "high"},
    }
    return _result(
        "unsubscribe_reply_classification",
        "Reply classifier identifies an explicit opt-out as unsubscribe.",
        checks,
        {"classification": result.get("classification"), "confidence": result.get("confidence")},
    )


def _with_isolated_queue(fn: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    """Run one queue scenario against a private in-memory SQLite database."""
    from gtm_engine.crm import db
    from gtm_engine.queues.queue_adapters import SQLiteQueueAdapter

    original_engine = db.engine
    isolated_engine = create_engine("sqlite:///:memory:", future=True)
    db.engine = isolated_engine
    try:
        return fn(SQLiteQueueAdapter())
    finally:
        db.engine = original_engine
        isolated_engine.dispose()


def _scenario_queue_idempotency() -> dict[str, Any]:
    def run(queue: Any) -> dict[str, Any]:
        from gtm_engine.queues.queue_adapters import BaseQueueAdapter

        key = BaseQueueAdapter.make_key(
            lead_id=42, channel="email", step=0,
            template_version="eval-v1", campaign_id="agent-task-eval",
        )
        first_id = queue.enqueue(42, {"type": "email", "to": "maya@example.test"},
                                 idempotency_key=key)
        duplicate_id = queue.enqueue(42, {"type": "email", "to": "maya@example.test"},
                                     idempotency_key=key)
        queued_before_dequeue = queue.pending_count()
        item = queue.dequeue()
        if item is not None:
            queue.ack(first_id, {"status": "sent_mock"})
        repeated_id = queue.enqueue(42, {"type": "email", "to": "maya@example.test"},
                                    idempotency_key=key)
        stats = queue.stats()
        checks = {
            "stable_idempotency_key": len(key) == 32,
            "duplicate_enqueue_returns_same_id": first_id == duplicate_id,
            "only_one_pending_task": queued_before_dequeue == 1,
            "dequeue_returns_original_task": item is not None and item["queue_id"] == first_id,
            "post_completion_duplicate_collapsed": repeated_id == first_id,
            "completed_once": stats.get("completed", 0) == 1,
            "no_pending_duplicate": queue.pending_count() == 0,
        }
        return _result(
            "queue_idempotency",
            "SQLite queue collapses repeated enqueue requests with the same idempotency key.",
            checks,
            {"idempotency_key": key, "first_queue_id": first_id,
             "duplicate_queue_id": duplicate_id, "repeated_queue_id": repeated_id,
             "queue_stats": stats},
        )
    return _with_isolated_queue(run)


def _scenario_queue_retry_lifecycle() -> dict[str, Any]:
    def run(queue: Any) -> dict[str, Any]:
        queue_id = queue.enqueue(77, {"type": "email", "to": "retry@example.test"},
                                 idempotency_key="agent-eval-retry-001", max_retries=2)
        first_attempt = queue.dequeue()
        queue.nack(queue_id, "simulated transient provider timeout")
        dlq_before_limit = queue.get_failed_ready_for_dlq()
        queue.retry(queue_id)
        second_attempt = queue.dequeue()
        if second_attempt is not None:
            queue.ack(queue_id, {"status": "sent_mock"})
        final_state = queue.peek(queue_id)
        checks = {
            "first_attempt_dequeued": first_attempt is not None and first_attempt["retry_count"] == 0,
            "failed_task_not_in_dlq_before_limit": len(dlq_before_limit) == 0,
            "retry_preserves_incremented_count": second_attempt is not None and second_attempt["retry_count"] == 1,
            "retry_reaches_completed_state": final_state is not None and final_state["status"] == "completed",
            "no_pending_task_after_completion": queue.pending_count() == 0,
        }
        return _result(
            "queue_retry_lifecycle",
            "Queue moves a failed task back to pending and completes it on retry without creating another task.",
            checks,
            {"queue_id": queue_id,
             "first_retry_count": first_attempt.get("retry_count") if first_attempt else None,
             "second_retry_count": second_attempt.get("retry_count") if second_attempt else None,
             "final_status": final_state.get("status") if final_state else None,
             "dlq_ready_before_retry": len(dlq_before_limit)},
        )
    return _with_isolated_queue(run)


def _scenario_retry_policy() -> dict[str, Any]:
    from gtm_engine.utils.retry import retry

    attempts = {"transient": 0, "permanent": 0}

    @retry(max_attempts=3, base_backoff=0, cap_backoff=0)
    def transient_call() -> str:
        attempts["transient"] += 1
        if attempts["transient"] < 3:
            raise TimeoutError("simulated transient timeout")
        return "ok"

    @retry(max_attempts=3, base_backoff=0, cap_backoff=0)
    def permanent_call() -> str:
        attempts["permanent"] += 1
        raise ValueError("simulated non-retryable validation error")

    transient_result = transient_call()
    permanent_error = None
    try:
        permanent_call()
    except ValueError as exc:
        permanent_error = str(exc)

    checks = {
        "transient_error_retried": attempts["transient"] == 3,
        "transient_call_eventually_succeeds": transient_result == "ok",
        "permanent_error_not_retried": attempts["permanent"] == 1,
        "permanent_error_propagated": permanent_error == "simulated non-retryable validation error",
    }
    return _result(
        "retry_policy",
        "Retry decorator retries transient timeouts but immediately propagates non-retryable errors.",
        checks,
        {"transient_attempts": attempts["transient"], "transient_result": transient_result,
         "permanent_attempts": attempts["permanent"], "permanent_error": permanent_error},
    )


SCENARIO_RUNNERS: tuple[Callable[[], dict[str, Any]], ...] = (
    _scenario_campaign_planning,
    _scenario_goal_structure,
    _scenario_budget_routing,
    _scenario_email_generation,
    _scenario_email_quality_approval,
    _scenario_spam_rejection,
    _scenario_unsubscribe_classification,
    _scenario_queue_idempotency,
    _scenario_queue_retry_lifecycle,
    _scenario_retry_policy,
)


def _git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
            text=True, cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_worktree_dirty() -> bool | None:
    """Return whether the working tree has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[2],
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def run_suite() -> dict[str, Any]:
    if not settings.mock_mode:
        raise RuntimeError("Agent-task evaluation must run with GTM_MOCK_MODE=true.")

    # Keep evaluation runs out of the application CRM's LLM usage table.
    from gtm_engine.clients import llm_client
    original_record_usage = llm_client._record_usage
    llm_client._record_usage = lambda *args, **kwargs: None
    results: list[dict[str, Any]] = []
    try:
        for runner in SCENARIO_RUNNERS:
            try:
                results.append(runner())
            except Exception as exc:  # Preserve scenario-level failure evidence.
                results.append({
                    "id": runner.__name__.removeprefix("_scenario_"),
                    "description": runner.__doc__ or runner.__name__,
                    "passed": False,
                    "checks": {"scenario_executed_without_exception": False},
                    "evidence": {"error_type": type(exc).__name__, "error": str(exc)},
                })
    finally:
        llm_client._record_usage = original_record_usage

    passed = sum(1 for item in results if item["passed"])
    total_checks = sum(len(item["checks"]) for item in results)
    passed_checks = sum(sum(bool(v) for v in item["checks"].values()) for item in results)
    return {
        "schema_version": 1,
        "suite": "autonomous_gtm_agent_task_eval",
        "mode": "deterministic_mock",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": _git_revision(),
        "working_tree_dirty": _git_worktree_dirty(),
        "environment": {"python": platform.python_version(), "mock_mode": settings.mock_mode},
        "summary": {
            "scenario_count": len(results),
            "scenario_passed": passed,
            "scenario_failed": len(results) - passed,
            "scenario_pass_rate": round(passed / len(results), 4) if results else 0.0,
            "check_count": total_checks,
            "checks_passed": passed_checks,
            "check_pass_rate": round(passed_checks / total_checks, 4) if total_checks else 0.0,
        },
        "limitations": [
            "LLM responses use deterministic mock mode; live model or planning quality is not measured.",
            "The email quality gate uses mock review heuristics, not an independent human review or recipient outcomes.",
            "Queue scenarios use in-memory SQLite and sequential duplicate submissions; Redis behavior and concurrent enqueue races are not tested.",
            "Retry checks validate classification and attempt counts with zero backoff; wall-clock backoff timing is not benchmarked.",
            "Budget routing is tested as the BudgetManager policy; runtime model selection in the LLM client is not asserted by this suite.",
            "LLM usage logging is suppressed in the evaluator to avoid writing evaluation traffic to the local CRM database.",
        ],
        "scenarios": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic GTM agent-task evaluations.")
    parser.add_argument(
        "--output", type=Path, default=Path("reports/agent_task_eval.json"),
        help="Path for the machine-readable JSON report (default: reports/agent_task_eval.json).",
    )
    args = parser.parse_args()

    report = run_suite()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = report["summary"]
    print("Autonomous GTM Engine — Agent Task Evaluation")
    print("=" * 52)
    for scenario in report["scenarios"]:
        status = "PASS" if scenario["passed"] else "FAIL"
        print(f"[{status}] {scenario['id']}: {scenario['description']}")
    print("-" * 52)
    print(f"Scenarios: {summary['scenario_passed']}/{summary['scenario_count']} passed")
    print(f"Checks:    {summary['checks_passed']}/{summary['check_count']} passed")
    print(f"Report:    {args.output}")
    return 0 if summary["scenario_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
