# Railway Production Deployment

Follow these steps to deploy to Railway:

1. **Provision Databases:**
   - Add a PostgreSQL instance.
   - Add a Redis instance.
2. **Deploy FastAPI App:**
   - Create a service pointing to your GitHub repo.
   - Set start command to `uvicorn gtm_engine.api:app --host 0.0.0.0 --port $PORT`.
   - Link PostgreSQL and Redis instances.
3. **Deploy Queue Workers:**
   - Create a separate service pointing to the same GitHub repo.
   - Set start command to `python -m gtm_engine.queues.worker`.
   - Ensure same environment variables (including `USE_REDIS=true` and `REDIS_URL`) are passed.
