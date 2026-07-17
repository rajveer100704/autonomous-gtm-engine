# Production Deployment Checklist

## 1. Prerequisites
- [ ] Git repository pushed to GitHub (`rajveer100704/autonomous-gtm-engine`).
- [ ] Active Railway (or Render/VM) account with billing configured.
- [ ] API keys provisioned for: Gemini, Tavily, Apollo, and SendGrid/Gmail.

## 2. Environment Variables Configuration
Ensure the following variables are configured in the cloud dashboard:
* `DATABASE_URL`: Production PostgreSQL connection string.
* `REDIS_URL`: Production Redis Streams connection string.
* `USE_REDIS`: `true`
* `WORKER_ID`: `production_worker_node_1`
* `GEMINI_API_KEY`: Google Gemini API credentials.
* `TAVILY_API_KEY`: Tavily Search API credentials.
* `APOLLO_API_KEY`: Apollo.io prospect discovery API key.
* `USE_OTEL`: `true` (if tracing is deployed)
* `OTEL_EXPORTER_OTLP_ENDPOINT`: OpenTelemetry endpoint (e.g. Jaeger collector url).

## 3. Deployment Order
1. **Provision Databases:** Deploy PostgreSQL and Redis services first.
2. **FastAPI API Service:** Deploy from repository root. Set start command to `uvicorn gtm_engine.api:app --host 0.0.0.0 --port $PORT`.
3. **Queue Worker Service:** Deploy as a separate worker service. Set start command to `python -m gtm_engine.queues.worker`.
4. **Observability Stack (Optional):** Deploy Jaeger, Prometheus, and Grafana containers.

## 4. Post-Deployment Verification
- [ ] Verify `GET /api/v1/health` returns `200 OK` (returns `{"status": "healthy"}`).
- [ ] Verify `GET /api/v1/ready` returns `200 OK` (returns `{"status": "ready", "database": "connected"}`).
- [ ] Verify `/metrics` exposes Prometheus client metrics.
- [ ] Enqueue a campaign task via POST to `/run-campaign` and verify queue worker consumes/completes it in database logs.

## 5. Rollback Strategy
1. **Application Rollback:**
   - Redeploy the previous successful container build from the Railway dashboard.
2. **Database:**
   - Keep migrations backward-compatible. No rollback required for schema.
3. **Queue Restoration:**
   - Redis Streams are persistent; workers restarting on the older version will safely resume stream consumption.
