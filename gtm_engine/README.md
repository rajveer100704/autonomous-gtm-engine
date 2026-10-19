# GTM Engine Package Notes

See the [root README](../README.md) for the project overview, architecture, local setup, benchmarks, current validation status, and deployment runbook.

## V5 Outcome-Driven Feedback Loop

V5 tracks first-touch email-style decisions against their exact outreach IDs. An outcome is eligible for learning only when it can be explicitly attributed to the same lead, outreach, and decision. Lead-only legacy events remain useful for funnel analytics but are not inferred onto an A/B variant.

The default attribution window is 14 days. Only eligible sent exposures that have reached the end of that window enter mature-cohort comparisons. Mature attributed email-reply rate is the primary metric; meeting and win rates are reported separately, while bounce and unsubscribe rates act as safety guardrails.

Policy evaluation uses minimum sample sizes, confidence intervals, configurable practical-lift thresholds, and safety checks. Recommendations are advisory; promotion is an explicit operator action. Synthetic simulator outcomes are excluded from real-data analysis by default and are not evidence of real-world uplift.

## Outcome and provider events

- Signed SendGrid Event Webhook endpoint: `POST /webhooks/sendgrid/events`.
- Signed Inbound Parse reply endpoint: `POST /webhooks/sendgrid/inbound`.
- Restricted one-message staging endpoint: `POST /provider/send-test`.
- Readiness endpoint: `GET /health/ready`.
- Feedback analytics: `GET /analytics/outcome-feedback`.
- Read-only recommendation: `GET /optimization/recommendation`.
- Explicit evaluation: `POST /optimization/evaluate`.

Provider receipts are deduplicated by provider event identity. A `delivered` event is not treated as an email reply. Inbound replies must be tied to the original sent outreach and matching learning decision. Keep sending disabled until the live staging provider setup is complete.

## Tests

From the repository root:

```powershell
python -m pytest gtm_engine/tests -q
python -m gtm_engine.optimization.report --seed 20261019
```

The PostgreSQL integration suite is `gtm_engine/tests/postgres_integration.py`. It requires `TEST_DATABASE_URL` to point to a disposable PostgreSQL database. Do not run it against production data. For the full command sequence and provider smoke test, see [`../PRODUCTION_VALIDATION_RUNBOOK.md`](../PRODUCTION_VALIDATION_RUNBOOK.md).

## Validation status

As of 19 October 2026, the recorded local run reports 135 tests passing and the separate PostgreSQL 16 integration suite reports 4 tests passing. GitHub Actions also passed the main suite and its PostgreSQL integration job for commit `3a47bc28bf8762322f55e0869203280164f112dd`. Live SendGrid delivery and inbound-reply validation remain pending. See the [root README](../README.md#deployment-and-live-validation-status) for the current limitations.
