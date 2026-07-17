# GTM Engine Evolution & Architectural Roadmap

This document outlines the architectural maturity model of the GTM Engine, tracking its journey from a localized automation script to a fully distributed, observable, and autonomous revenue operating system.

---

## 1. Architectural Maturity Model

| Version | Focus | Core Architectural Change | Outcome |
| :--- | :--- | :--- | :--- |
| **V1** | **AI Generation** | Linear prompt pipelines (Apollo → Tavily → Gemini) | Personalized cold copy |
| **V2** | **Agentic Execution** | LangGraph orchestration, state management, persistent queue | Stateful autonomous workflows |
| **V3** | **Production Reliability** | Circuit breakers, DLQ, retry decorators, idempotency, audit spans | Fault-tolerant execution gates |
| **V4.1** | **Observability & Decoupling** | Feature flags, unified ObservabilityService, Domain Event Bus, API versioning | Traceable, decoupled core |
| **V4.2** | **Distributed Platform** | Redis Streams, Queue Adapters, Goal Manager, MCP Tool Registry | Observable, horizontally scalable execution |
| **V4.3** | **Enterprise SaaS** | JWT Auth, RBAC, workspaces/organizations, Admin Ops Center | Multi-tenant SaaS platform |
| **V5** | **Autonomous Revenue OS**| Closed-loop ReAct loops, self-optimizing business KPIs | Goal-driven self-improving platform |

---

## 2. Platform Journey

### V1 — AI Content Generation
**Goal:** Generate highly personalized cold outreach.
- **Pipeline:** `Lead` → `Apollo Prospecting` → `Grounded Research` → `Pain Point Extraction` → `Email/LinkedIn Copy Generation`.
- **Capability:** Automated data enrichment and basic personalization.

### V2 — Agentic Orchestration
**Goal:** Execute campaigns autonomously.
- **Pipeline:** `Planner` → `Strategy Agent` → `Execution Queue` → `Async Workers` → `CRM Sync`.
- **Capability:** LangGraph state machine tracking campaign progression, checkpoint storage, resume/interrupt capability, and persistent CRM logging.

### V3 — Production Reliability
**Goal:** Guarantee system resilience and fault tolerance.
- **Pipeline:** `Parallel Intelligence Gatherer` → `Two-Stage Evaluation Gate` → `Idempotency Guard` → `Retry with Jitter` → `Circuit Breakers` → `DLQ Escalation` → `Audit Logging`.
- **Capability:**
  - Parallel sub-agents (News, Hiring, Website, Tech Stack) run concurrently.
  - Generative outputs gate-checked via deterministic rules followed by semantic LLM scoring.
  - Resilience patterns protect external services (Gmail, LinkedIn browser engines).

---

## 3. V4.1 — Production Infrastructure & Observability (Completed)
**Goal:** Make the V3 agentic application observable, decoupled, and deployable.
- **Feature Flags (`utils/feature_flags.py`):** Configurable flags (`USE_REDIS`, `USE_OTEL`, `USE_MCP`, `USE_REACT`, `USE_MULTI_TENANT`) and profiles (`development`, `staging`, `production`, `demo`).
- **ObservabilityService (`observability/observability_service.py`):** Unified instrumentation wrapper that gracefully degrades to local fallback logging if OTel is disabled or unavailable.
- **Domain Event Bus (`events/domain_events.py`):** Decoupled Worker dispatch from CRM, lead memory, and analytics. Workers emit `EMAIL_SENT` / `LINKEDIN_SENT` events; subscribers update records and append contact history.
- **API Versioning (`api.py`):** Mounted routes under `/api/v1` with root-level fallbacks for backward-compatibility.
- **Operations & Health Checks:** Implemented `/live` and `/ready` endpoints, and exposed Prometheus `/metrics` scraping.
- **Observability Containers:** Configured Jaeger OTel tracing collector, Prometheus metrics scraper, and Grafana dashboard services.

---

## 4. V4.2 — Distributed Platform (Next Milestone)
**Goal:** Transition the platform from localized async queues to distributed parallel workers.

### Milestone 1 — Performance & Load Testing
- **Goal:** Benchmark the system before moving queue backends.
- **Components:** `benchmark/load_test.py`, `benchmark/worker_scaling.py`.
- **Metrics Measured:** Queue throughput, planner latency, worker CPU/memory utilization, event processing latency.

### Milestone 2 — Event Store & Replayability
- **Goal:** Implement an Event Store to persist all published Domain Events.
- **Outcome:** Allows replaying events for analytical debugging or state reconstruction after worker crashes.

### Milestone 3 — Pluggable Queue Adapters
- **Goal:** Abstract the execution queue behind a unified Queue Interface.
- **Adapters:** SQLite Adapter (local) and Redis Streams Adapter (production). A `queue_factory` resolves the adapter dynamically via feature flags.

### Milestone 4 — Goal Manager & Adaptive ReAct Planner
- **Goal:** Separate campaign-level logic from outreach execution.
- **Goal Manager:** Tracks Campaign Goals (e.g. "Book 5 Demos"). Decides when to follow up, send a connection request, pause campaign, or escalate to manual sales reps.
- **Strategy Agent:** Retains singular focus on *how* to draft personalized outreach based on chosen strategies.

### Milestone 5 — MCP Tool Registry & Metadata Permissions
- **Goal:** Standardize how external services (Gmail, Tavily, LinkedIn) are exposed to agents.
- **Tool Schema:**
  ```json
  {
    "tool": "gmail.send",
    "description": "Send outreach email",
    "requires": ["gmail.send"],
    "timeout": 30,
    "retry": true
  }
  ```
- **Permissioning:** Restricts tool access based on agent scopes, preventing prompt injection exploits.

---

## 5. V4.3 — Enterprise SaaS
**Goal:** Add SaaS platform abstractions, organizations, and user billing support.

- **Workspace & Tenancy:** DB isolation mapping `Workspace -> Organization -> Campaign -> Lead`.
- **JWT & Role-Based Access Control (RBAC):** Authenticate users and restrict dashboard configurations based on roles (`admin`, `member`).
- **Secrets Management:** Pluggable integration with AWS Secrets Manager or HashiCorp Vault to store client Gmail OAuth tokens and Tavily keys securely.
- **Admin Operations Center:** Full dashboard monitoring and managing campaign pauses/resumes, worker fleets, DLQ requeue actions, and active circuit breaker states.
- **API Versioning Evolution:** Introduce `/api/v2` routes for new SaaS models while maintaining fully compatible version gateways.

---

## 6. V5 — Autonomous Revenue OS
The final maturity stage transitions the system from an execution workflow into a **closed-loop self-improving platform**:
- **Continuous Optimization:** The platform analyzes which industries/personas respond to which pitches.
- **Feedback Loop:** Automatically updates the campaign planner's prompt strategy to maximize meetings booked without manual configuration.
