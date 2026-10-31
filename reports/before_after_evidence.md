# Autonomous GTM Engine ? Before/After Evidence

Generated: 2026-10-31T01:32:26.118558+00:00

## Test results

| Metric | Original baseline | Updated branch |
|---|---:|---:|
| Tests passed | 135 | 137 |
| Warnings | 72 | 94 |
| Pytest duration | 101.82s | 84.52s |
| Wall-clock duration | 115.32s | 91.70s |

## Observed differences

- Tests passed: +2
- Warnings: +22
- Pytest duration: -17.30s (-16.99%)
- Wall-clock duration: -23.62s (-20.48%)

These are single sequential measurements, not a statistically controlled performance benchmark. Do not interpret the shorter observed runtime as a proven optimization.

## Agent-task evaluation

- Mode: `deterministic_mock`
- Scenarios passed: 10/10
- Checks passed: 46/46
- Evaluation source revision: `f4138f7805c8b1bfc6117a1dfe27022bfb379a11`
- Working tree dirty during evaluation: `True`

## Slowest tests

### Original baseline

- 18.31s (call): `gtm_engine/tests/test_outcome_feedback.py::test_explicit_evaluation_promotes_with_strong_real_attributed_evidence_idempotently`
- 6.62s (call): `gtm_engine/tests/test_feedback_simulator.py::test_equal_effect_negative_control_does_not_force_policy_uplift`
- 2.87s (setup): `gtm_engine/tests/test_agents_platform.py::test_execution_queue_enqueue_dequeue`
- 2.38s (setup): `gtm_engine/tests/test_agents_platform.py::test_lead_memory_returns_empty_for_new_lead`
- 2.30s (call): `gtm_engine/tests/test_agents_platform.py::test_lead_memory_persists_and_appends`

### Updated branch

- 19.99s (call): `gtm_engine/tests/test_outcome_feedback.py::test_explicit_evaluation_promotes_with_strong_real_attributed_evidence_idempotently`
- 7.89s (call): `gtm_engine/tests/test_feedback_simulator.py::test_equal_effect_negative_control_does_not_force_policy_uplift`
- 2.82s (call): `gtm_engine/tests/test_pipeline.py::test_ab_test_does_not_promote_without_explicit_mature_attribution`
- 2.25s (call): `gtm_engine/tests/test_feedback_simulator.py::test_segment_policy_learns_heterogeneous_preferences_with_enough_training_data`
- 1.65s (call): `gtm_engine/tests/test_feedback_simulator.py::test_report_has_seeded_positive_and_negative_controls`

## Methodology and limitations

- Both runs used Python 3.14.0 on the same machine.
- Both used mock mode with Redis and OpenTelemetry disabled.
- The baseline ran first, followed by the updated branch.
- Each revision was measured once.
- The agent-task suite uses deterministic mocked LLM responses and SQLite.
- It does not measure live provider quality, actual email delivery, Redis concurrency races, or campaign conversion.
- The warning increase is recorded for follow-up; warnings are not silently treated as failures.

The machine-readable version of this report is `before_after_evidence.json`.

## Source revision provenance

- Baseline revision: `f4138f7805c8b1bfc6117a1dfe27022bfb379a11`
- Updated branch: `feat/agent-task-evaluation`
- Updated tree base revision: `f4138f7805c8b1bfc6117a1dfe27022bfb379a11`
- Updated feature changes were uncommitted during measurement; no commit SHA is claimed for that snapshot.
