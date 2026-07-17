# Local Docker Deployment

Run the entire platform locally with Docker Compose:

```bash
# Build and boot services (api, db, redis, worker, scheduler, dashboard, jaeger, prometheus, grafana)
docker compose up --build -d

# Verify all services are running
docker compose ps
```

### Verification Endpoints
- **FastAPI API:** `http://localhost:8000/api/v1/health`
- **Dashboard:** `http://localhost:8501`
- **Jaeger UI:** `http://localhost:16686`
- **Prometheus:** `http://localhost:9090`
- **Grafana:** `http://localhost:3000` (admin/admin)
