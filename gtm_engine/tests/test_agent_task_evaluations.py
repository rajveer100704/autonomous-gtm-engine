"""Regression tests for the deterministic GTM agent-task evaluation suite."""

from gtm_engine.evals.agent_task_eval import run_suite


def test_agent_task_evaluation_suite_passes_all_scenarios():
    report = run_suite()

    assert report["suite"] == "autonomous_gtm_agent_task_eval"
    assert report["mode"] == "deterministic_mock"
    assert report["summary"]["scenario_count"] == 10
    assert report["summary"]["scenario_failed"] == 0, report["scenarios"]
    assert report["summary"]["scenario_pass_rate"] == 1.0
    assert report["summary"]["check_pass_rate"] == 1.0


def test_agent_task_evaluation_report_discloses_mock_limitations():
    report = run_suite()
    limitations = " ".join(report["limitations"]).lower()

    assert "live model or planning quality is not measured" in limitations
    assert "redis behavior" in limitations
    assert "recipient outcomes" in limitations
    assert "crm database" in limitations
