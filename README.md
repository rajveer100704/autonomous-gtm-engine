# Autonomous GTM Engine (Platform Edition)

**An event-driven, multi-agent go-to-market automation platform** built with FastAPI, LangGraph, pluggable SQLite/Redis queues, PostgreSQL persistence, outcome-driven policy evaluation, and OpenTelemetry observability.

> **Validation status — 19 October 2026:** the latest local suite completed with **135 tests passing**; the PostgreSQL 16 integration suite completed with **4 tests passing**; and GitHub Actions passed, including its PostgreSQL job. The staging readiness endpoint has also reported a healthy PostgreSQL connection. **A real SendGrid delivery-and-reply smoke test is still pending**, so this README does not claim live provider validation or real-world campaign uplift.

---

## What this is

Autonomous GTM Engine is an **agentic GTM operating system**, rather than only an AI copy generator. It coordinates lead research and enrichment, strategy selection, outreach preparation, quality checks, execution, interaction memory, analytics, and outcome feedback through a modular workflow.

The repository focuses on production-oriented engineering patterns: explicit goals and constraints, recoverable tasks, idempotent event ingestion, database-backed attribution, safety checks, traceability, and evidence-gated policy recommendations.

## Key capabilities

| Category | Capability | Implementation notes |
|---|---|---|
| AI orchestration | LangGraph `StateGraph` | Stateful campaign graph, checkpointing, recovery paths, and conditional/A-B routing. |
| Planning and budget | Goal Manager and budget-aware routing | Parses campaign objectives, success criteria, constraints, budgets, and deadlines. |
| Quality controls | Evaluation gate | Deterministic validation plus semantic scoring before eligible actions proceed. |
| Execution | Pluggable queue provider | Supports SQLite and Redis Streams queue adapters through a shared interface. |
| Worker reliability | Worker fleet | Heartbeats, retries/recovery, and circuit-breaker patterns. |
| Event history | Event store and audit metadata | Correlation/causation context for following execution and outcomes. |
| Observability | OpenTelemetry, Prometheus, Grafana, Jaeger | Instrumentation and integration points for traces and metrics. Deploy and configure the supporting services separately. |
| Tooling | MCP tool registry | Tool capability schemas, timeouts, permissions, and cost controls. |
| Memory | Lead memory | Retains relevant prior interactions and classifications for follow-up decisions. |
| Outcome feedback | V5 policy evaluation | Attributes outcomes to exact outreach/decision records, evaluates mature cohorts, and produces advisory recommendations with evidence and safety gates. |
| Provider ingestion | Signed SendGrid hooks | Event Webhook receipts and Inbound Parse reply attribution are implemented; live provider validation remains pending. |
| Staging safety | One-message smoke-send endpoint | `/provider/send-test` is staging-only and requires explicit enablement, authorization, and an exact recipient allowlist. Keep it disabled until provider setup is complete. |

---

## V5 outcome-driven feedback loop

V5 closes the loop between outreach decisions and observed outcomes:

1. **Decision-level attribution.** A learning outcome must be tied to the relevant lead, outreach, and decision. Legacy lead-only events are not guessed onto an A/B variant.
2. **Mature cohorts.** The default attribution window is 14 days; only eligible sent exposures that have reached the end of the window enter mature-cohort comparisons.
3. **Decision metrics and guardrails.** Mature attributed reply rate is the primary metric. Meeting and win rates are reported separately; bounce and unsubscribe rates act as safety guardrails.
4. **Evidence-gated recommendations.** Evaluation uses sample-size requirements, confidence intervals, practical-lift thresholds, and safety checks. Recommendations are advisory; promotion is an explicit operator action.
5. **Simulation separation.** Synthetic simulator events are excluded from real-data analysis by default. Synthetic benchmark results are not evidence of real-world campaign uplift.
6. **Durable provider ingestion.** Signed SendGrid Event Webhook and Inbound Parse handlers use durable provider-event receipts and idempotency protection. A `delivered` event is not interpreted as an email reply.

### Important API routes

| Route | Purpose |
|---|---|
| `GET /health/ready` | Readiness check including a database connectivity probe. |
| `GET /analytics/outcome-feedback` | Outcome-feedback analytics. |
| `GET /optimization/recommendation` | Read-only policy recommendation. |
| `POST /optimization/evaluate` | Explicit policy evaluation/promotion workflow. |
| `POST /webhooks/sendgrid/events` | Signed SendGrid delivery/engagement events. |
| `POST /webhooks/sendgrid/inbound` | Signed inbound replies, correlated to the original outreach. |
| `POST /provider/send-test` | Restricted one-message staging smoke send; disabled by default. |

See [`gtm_engine/README.md`](gtm_engine/README.md) for the attribution semantics and implementation details, and [`PRODUCTION_VALIDATION_RUNBOOK.md`](PRODUCTION_VALIDATION_RUNBOOK.md) for the provider validation procedure.

---

## Architecture

```text
                         FastAPI API
                             |
                             v
              Goal Manager / Budget Controls
                             |
                             v
                    LangGraph Campaign
                             |
                             v
                    MCP Tool Registry
                             |
                             v
                     Queue Provider
                       /        \
                      v          v
                SQLite Queue   Redis Streams
                       \        /
                             v
                         Worker Fleet
                       /            \
                      v              v
                 Gmail Sender   LinkedIn Executor
                      |
                      v
               Domain Events / Event Store
                      |
         +------------+-------------+-------------+
         |            |             |             |
         v            v             v             v
        CRM        Analytics     Lead Memory   Audit Log
                      |
                      v
             V5 Outcome Feedback
                      ^
                      |
        Signed SendGrid Event / Inbound Webhooks
                      |
                      v
             Durable Event Receipts

       OpenTelemetry -> Prometheus / Grafana / Jaeger
```

The observability and queue backends require their respective services and environment configuration; the diagram describes integration points, not an assertion that every optional service is running in every deployment.

---

## Repository structure

```text
gtm_engine/
├── agents/             # Goal Manager, Planner, Strategy, Evaluation, research agents
├── audit/              # Structured audit logging
├── benchmark/          # Queue throughput/latency benchmark suite
├── chaos/              # Chaos simulation and recovery tests
├── crm/                # Database schema, persistence, and CRM operations
├── events/             # Event bus and event store
├── evals/              # Deterministic agent-task evaluation scenarios and JSON reports
├── executors/          # Gmail, LinkedIn, and SendGrid sending adapters
├── mcp/                # MCP tool registry and capability schemas
├── observability/      # OpenTelemetry and Prometheus integration
├── optimization/       # Outcome feedback, policy evaluation, simulator, and reports
├── providers/          # Signed SendGrid webhook and inbound-reply handlers
├── queues/             # SQLite and Redis Streams queue adapters
├── tests/              # Unit, platform, webhook, and PostgreSQL integration tests
├── utils/              # Feature flags, circuit breakers, retry utilities
└── api.py              # FastAPI routes, health, analytics, and provider hooks
```

---

## Run locally

Use a virtual environment and install the project dependencies from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r gtm_engine/requirements.txt
```

For a safe local test profile, keep mock mode on and optional infrastructure dependencies disabled:

```powershell
$env:GTM_MOCK_MODE = "true"
$env:USE_REDIS = "false"
$env:USE_OTEL = "false"
python -m pytest -v
```

To start the API locally:

```powershell
python -m uvicorn gtm_engine.api:app --host 127.0.0.1 --port 8000
```

The default configuration and any required credentials depend on the features you enable. Do not put provider keys, database passwords, or application secrets in source control.

## Tests and benchmarks

Run the main test suite:

```powershell
python -m pytest -v
```

Run the dedicated PostgreSQL integration suite against a **disposable PostgreSQL 16 test database**. It requires `TEST_DATABASE_URL`; never point it at production data. The GitHub Actions workflow runs this suite in its own PostgreSQL service container:

```powershell
python -m pytest -v --tb=short gtm_engine/tests/postgres_integration.py
```

See [`PRODUCTION_VALIDATION_RUNBOOK.md`](PRODUCTION_VALIDATION_RUNBOOK.md) for the complete disposable-container command sequence.

Run the local queue benchmark:

```powershell
python -m gtm_engine.benchmark.report
```

Run the deterministic synthetic policy benchmark:

```powershell
python -m gtm_engine.optimization.report --seed 20261019
```

Run chaos/recovery checks:

```powershell
python -m gtm_engine.chaos.chaos_test
```

Run the deterministic agent-task evaluation suite (mock mode only):

```powershell
$env:GTM_MOCK_MODE = "true"
$env:USE_REDIS = "false"
$env:USE_OTEL = "false"
python -m gtm_engine.evals.agent_task_eval --output reports/agent_task_eval.json
```

The suite covers campaign planning, goal constraints, budget model-selection policy,
email draft personalization, email quality approval/rejection, unsubscribe classification,
queue idempotency, queue retry lifecycle, and transient-versus-permanent retry behavior.
It uses mocked LLM responses and an isolated in-memory SQLite queue. Its results are
functional regression evidence only—not live model quality, provider delivery, or
real-world campaign outcomes.


### Recorded validation snapshot

The validation run recorded on 19 October 2026 reported **135 tests passing** locally and **4 PostgreSQL integration tests passing** against PostgreSQL 16. GitHub Actions passed the lint, main test, PostgreSQL integration, Compose validation, Docker build, and artifact-upload jobs for the staging-guard commit `3a47bc28bf8762322f55e0869203280164f112dd`. Test counts and CI status can change as the repository evolves; check the [latest workflow runs](https://github.com/rajveer100704/autonomous-gtm-engine/actions).

### Queue performance profile

The recorded SQLite local profile reported:

- Enqueue throughput: approximately 2,170 tasks/second
- Dequeue throughput: approximately 1,940 tasks/second
- Average enqueue latency: 0.47 ms
- Average dequeue latency: 0.46 ms
- Average acknowledgement latency: less than 0.01 ms

Recorded environment: Intel Core i7-12700K, 32 GB DDR4, Python 3.14.0, SQLite 3.45.2, Redis 7.2.4 in Docker Compose, and one worker for the local synchronous adapter profile. These are **local-profile measurements**, not a guarantee of hosted production throughput.

The policy benchmark is separate. Its simulator output is synthetic; do not report its differences as real-world campaign uplift.

---

## Deployment and live validation status

A Render staging service was deployed at [`https://autonomous-gtm-engine-staging.onrender.com`](https://autonomous-gtm-engine-staging.onrender.com). On 19 October 2026, `GET /health/ready` returned `status=ready`, `database=reachable`, `database_url_kind=postgres`, and `environment=staging`.

**Live SendGrid end-to-end validation is not yet complete.** A real delivered callback and real inbound reply have not been recorded as verified. Completion requires an active SendGrid sending account, a verified sender/domain, signed Event Webhook configuration, an authenticated Inbound Parse subdomain with DNS/MX records, staging secrets, and the single-recipient smoke test. A staging readiness check is not proof that email delivery or reply attribution works.

Until that live test is complete:

- Keep `GTM_MOCK_MODE=true` and `ENABLE_PROVIDER_SENDS=false` in staging.
- Do not run a real campaign as a smoke test.
- Keep secrets in the host's secret manager, never in the repository.
- Rotate credentials if they have been exposed.
- Do not claim real-world uplift from synthetic simulator results.

Use [`PRODUCTION_VALIDATION_RUNBOOK.md`](PRODUCTION_VALIDATION_RUNBOOK.md) for the exact webhook configuration, one-message test, signature-rejection checks, database assertions, and idempotency verification. Render deployment notes are in [`docs/deployment/RENDER.md`](docs/deployment/RENDER.md).
