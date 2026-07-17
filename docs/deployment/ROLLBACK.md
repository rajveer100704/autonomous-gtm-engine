# Deployment Rollback Strategy

If a deployment fails health checks or exhibits runtime issues, follow this rollback procedure:

1. **API / Worker Services:**
   - Revert to the previously tagged release (e.g. `v4.2` tag or previous GitHub commit SHA).
   - In Railway, redeploy the previous successful build from the dashboard.
2. **Database:**
   - Database migrations are backward-compatible. Reverting the application process does not require downgrading Postgres schemas.
3. **Queue Tasks:**
   - In-flight tasks in Redis Streams can be recovered by workers restarting on the old version since stream structures are persistent and backward-compatible.
