# V5 Outcome-Driven Feedback Loop — Implementation Report

## Validation performed

The base V5 implementation was validated in the repository checkout. This follow-up patch adds signed SendGrid event ingestion, signed inbound-reply attribution, an explicitly gated one-message staging sender, and PostgreSQL integration coverage.

- `python -m compileall -q gtm_engine` — passed after the follow-up patch.
- `GTM_MOCK_MODE=true USE_REDIS=false USE_OTEL=false python -m pytest -q` — **133 passed, 0 failed** after adding provider/security tests.
- The PostgreSQL integration suite is implemented in `gtm_engine/tests/postgres_integration.py` and is wired to a PostgreSQL 16 CI service. It was **not executed in this isolated environment**, where Docker/PostgreSQL are unavailable. Run it locally or verify the GitHub Actions job before claiming PostgreSQL validation passed.
- A live SendGrid send/event callback/inbound-reply cycle requires staging credentials, a public HTTPS deployment, separate provider webhook verification keys, and Inbound Parse DNS/MX configuration; it was **not executed here**.
- The synthetic benchmark remains synthetic; no real campaign uplift is claimed.

The SQLite test run emitted 72 non-fatal warnings, principally the deprecated SQLite datetime adapter and legacy uses of `datetime.utcnow()`. They did not cause test failures.

## Main implementation areas

- Added additive `learning_decisions`, `learning_outcomes`, and validated-promotion persistence, using the existing SQLAlchemy Core database layer.
- Tightened decision/outreach/lead matching, outcome event-key idempotency, sent-exposure eligibility, attribution-window checks, mature cohort filtering, and synthetic-data separation.
- Kept the backwards-compatible `assign_variant()` API while adding probability-bearing assignments and requiring a V5-validated winner for changed live allocation.
- Instrumented the linear orchestrator and LangGraph content path with outreach-linked decisions and retry reuse; added campaign-run context.
- Extended event tracking with optional explicit attribution fields and added read-only outcome-feedback/recommendation endpoints plus an explicit evaluation endpoint.
- Added an Outcome Feedback view in Streamlit and labelled synthetic/demo behavior.
- Added deterministic local simulation, shared held-out evaluation data, randomized/global/segment/oracle policy comparison, a negative-control cohort, and reproducible JSON/Markdown reports.
- Added V5-focused tests and updated the legacy test that previously expected lead-only reply events to promote a winner.
- Updated README and roadmap documentation to keep recommendations advisory and synthetic benchmark caveats explicit.

## Seeded synthetic benchmark results

Command: `python -m gtm_engine.optimization.report --seed 20261019`

Five positive-control scenarios were run, each with 3,000 synthetic training records and 2,000 synthetic held-out records. These are generator-derived test results, **not real campaign evidence**.

| Policy | Mean held-out reply rate | Mean meeting rate | Mean won rate | Mean difference vs randomized baseline | Mean regret vs oracle |
|---|---:|---:|---:|---:|---:|
| Randomized baseline | 22.44% | 8.49% | 1.85% | 0.00 pp | 8.35 pp |
| Global-best | 23.19% | 8.52% | 1.87% | +0.75 pp | 7.60 pp |
| Segment-aware | 27.49% | 10.32% | 2.41% | +5.05 pp | 3.30 pp |
| Oracle upper bound (not deployable) | 30.79% | 11.78% | 2.85% | +8.35 pp | 0.00 pp |

The equal-effect negative control had a mean segment-aware reply-rate difference of +0.57 percentage points over the randomized baseline across five seeded scenarios. Individual finite-sample differences varied; these results are a regression check, not a formal claim of a production false-positive rate. The oracle uses simulator ground truth and is never a deployable policy.

## Caveats and release notes

- The uploaded snapshot did not include a `.git` directory, so repository-relative `git diff --check` and a push were not performed in this environment.
- Existing non-fatal datetime deprecation warnings remain.
- Real provider callbacks and production/staging deployment behavior remain unverified until the runbook's live checks complete. The PostgreSQL CI job must pass before claiming PostgreSQL integration verification.
- No real outbound messages were sent by the tests or simulator. The staging-only `/provider/send-test` endpoint is disabled by default and requires an exact allowlist.
- This follow-up package is not yet committed or pushed. Apply it to the existing Git checkout while preserving `.git` and local credentials, run the local PostgreSQL suite, and push only after review/validation.
