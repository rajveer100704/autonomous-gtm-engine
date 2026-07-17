# Deployment Audit — Autonomous GTM Engine

This document outlines all blockers and issues preventing successful production deployment of the Autonomous GTM Engine platform.

---

## 🚫 Critical Blockers (Must Fix)

### 1. Missing Dependencies in `requirements.txt`
The following core packages imported by the platform logic are missing from `requirements.txt`:
* `redis` (Required by `RedisQueueAdapter`)
* `prometheus-client` (Required by metrics endpoint in `api.py`)
* `opentelemetry-api` (Required by observability framework)
* `opentelemetry-sdk` (Required by observability framework)
* `opentelemetry-exporter-otlp` (Required by Jaeger connection)
* `opentelemetry-instrumentation-fastapi` (Required for HTTP trace logging)

### 2. Docker Compose Build Context Mismatch
* `Dockerfile` is stored inside `gtm_engine/Dockerfile`.
* The `docker-compose.yml` specifies `build: .`, looking for a root-level `Dockerfile`.
* **Fix:** Update `docker-compose.yml` build configurations to point to `dockerfile: gtm_engine/Dockerfile` with `context: .`, or move the `Dockerfile` to the root workspace.

### 3. Missing Redis & Worker Services in Docker Compose
* Local docker-compose configuration contains `api`, `db`, `scheduler`, `dashboard`, `jaeger`, `prometheus`, and `grafana`.
* It lacks a `redis` stream broker service and `worker` service to run the execution queue in distributed mode.

### 4. Missing Environment Variables in `.env.example`
The following operational keys needed for V4.2 distributed features are missing from `.env.example`:
* `USE_REDIS` (boolean switch)
* `REDIS_URL` (redis connection string)
* `OTEL_EXPORTER_OTLP_ENDPOINT` (OpenTelemetry endpoint)
* `WORKER_ID` (process identity)

### 5. Missing /health Endpoint
* The platform's routing exposes `/api/v1/live` and `/api/v1/ready`.
* The standard `/api/v1/health` endpoint required by typical load balancers is not implemented in `api.py`.

---

## ⚠️ Non-Blocking Warnings

### 1. Streamlit Dashboard Build Context
* The `dashboard` service inside `docker-compose.yml` attempts to execute: `streamlit run gtm_engine/dashboard.py`
* When built, the context directory structure copy must ensure `gtm_engine` packages are correctly configured in `PYTHONPATH`.

### 2. Missing Deployment Documentation
* Neither root `README.md` nor package `README.md` details environment variable setups, Docker build steps, or database migration rules.
