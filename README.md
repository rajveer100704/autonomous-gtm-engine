# Autonomous GTM Engine (Platform Edition)

**An enterprise-grade, event-driven autonomous outreach engine built with LangGraph orchestration, event-sourced persistence, pluggable Redis execution queues, OpenTelemetry observability, and a cost-aware Goal Manager.**

---

## What This Is

An **Agentic GTM Operating System** — not a cold copy generator. The platform autonomously discovers leads, builds deep parallel company intelligence, decodes the optimal outreach strategy per lead, executes across communication channels, and maintains long-term memory of every interaction for future campaigns.

## Why This Project?
Modern outbound systems require more than just raw AI-generated outreach copy. To operate reliably at scale, they require robust workflow orchestration, decoupled task execution queues, persistent event-sourcing, strict evaluation checks, and comprehensive telemetry observability. This project demonstrates how these production-grade software engineering concerns can be elegantly unified into a modular, multi-agent enterprise platform.

---

## 🚀 Key Capabilities

| Category | Capability | Architectural Implementation |
| :--- | :--- | :--- |
| **AI Orchestration** | **LangGraph StateGraph** | Stateful multi-agent graph with SQLite checkpointing, recovery nodes, and conditional A/B split routing. |
| | **Goal Manager Node** | Declarative campaign gate parsing objectives into structured schemas (Success Criteria, Constraints, Budget, Deadlines). |
| | **Budget Manager** | Dynamic cost-aware routing (routing research/evals to cheap models, strategy/copy to best models). |
| | **Evaluation Gate** | Two-stage quality control (deterministic validation + semantic scoring). |
| **Platform Systems** | **Queue Provider (DI)** | Pluggable, lazy-loaded queue system supporting `SQLiteQueueAdapter` and `RedisQueueAdapter` backends. |
| | **Worker Fleet** | Horizontally scalable async consumers with task heartbeats, crash recovery, and circuit breakers. |
| | **Event Store** | Event Sourced append-only log tracing correlation and causation metadata across planners. |
| **Observability** | **Full Traceability** | OpenTelemetry exporter wired to Jaeger, Prometheus scraping metrics, and pre-built Grafana dashboards. |
| | **Audit Log Spans** | Structured immutable audit log utilizing spans to time and track execution costs. |
| **Integrations** | **MCP Tool Registry** | Standardized tool capability catalog with schemas, timeout rules, permissions, and cost limits. |
| | **Lead Memory** | Append-only lead memory storing past emails, classifications, and objection responses. |

---

## 📈 Performance Benchmarks
We built an automated performance benchmarking suite (`gtm_engine/benchmark/`) to measure throughput and latency under pressure.

### SQLite Queue Throughput & Latency (Local Profile)
- **Enqueue Throughput:** ~2,170 tasks/second
- **Dequeue Throughput:** ~1,940 tasks/second
- **Average Enqueue Latency:** 0.47 ms
- **Average Dequeue Latency:** 0.46 ms
- **Average Ack Latency:** < 0.01 ms

#### Benchmark Environment
- **CPU:** Intel Core i7-12700K (12 Cores, 20 Threads)
- **RAM:** 32 GB DDR4
- **Python Version:** 3.14.0
- **SQLite Version:** 3.45.2
- **Redis Version:** 7.2.4 (running in Docker Compose)
- **Number of Workers:** 1 (local sync run for local adapter profiles)

---

## 🛠️ System Architecture

```
                 API Gateway (FastAPI)
                      │
                      ▼
                Goal Manager (Budget / Deadline Controls)
                      │
                      ▼
                LangGraph Campaign Graph
                      │
                      ▼
              MCP Tool Registry (Tool Schemas)
                      │
                      ▼
            QueueProvider (DI Interface)
                      │
        ┌─────────────┴──────────────┐
        ▼                            ▼
 SQLiteQueueAdapter        RedisQueueAdapter (Streams)
        │                            │
        └─────────────┬──────────────┘
                      ▼
                 Worker Fleet
                      │
         ┌────────────┴────────────┐
         ▼                         ▼
    Gmail Executor         LinkedIn Executor (Playwright)
         │                         │
         └────────────┬────────────┘
                      ▼
                Domain Events (Pub/Sub)
                      │
                      ▼
                 Event Store (Event Sourcing)
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
      CRM        Analytics     Lead Memory
                      │
                      ▼
             ObservabilityService (OTel)
                      │
                      ▼
     Prometheus ──> Grafana ──> Jaeger Tracing
```

---

## 📁 Repository Structure

```
gtm_engine/
├── agents/             # Goal Manager, Planner, Strategy, Evaluation, Calendar
├── audit/              # Structured Immutable Audit logging
├── benchmark/          # Throughput and Latency benchmarking suite
├── chaos/              # Chaos simulation and worker recovery validation
├── crm/                # SQL Schema, DB Engine, and CRM service operations
├── docs/               # Architecture Decision Records (ADRs) and Diagrams
├── events/             # Event Bus and Event Sourcing Event Store
├── executors/          # Gmail API, LinkedIn Browser, and Search executors
├── mcp/                # MCP Tool Registry and capability catalog
├── observability/      # OpenTelemetry and Prometheus Metric exporters
├── queues/             # Pluggable SQLite and Redis Stream Queue adapters
├── utils/              # Feature flags, circuit breakers, and retry logic
└── api.py              # FastAPI versioned routes, health, and metrics
```

---

## 🧪 Running the Test Suite
Ensure all 87+ unit and platform tests are passing:
```powershell
python -m pytest -v
```

To run the automated performance benchmark suite:
```powershell
python -m gtm_engine.benchmark.report
```

To validate chaos recovery:
```powershell
python -m gtm_engine.chaos.chaos_test
```
