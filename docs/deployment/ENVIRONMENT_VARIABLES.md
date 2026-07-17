# Environment Variables

Configure the following variables in production:

### Core Platform Config
- `DATABASE_URL`: Connection string for PostgreSQL.
- `REDIS_URL`: Connection string for Redis Streams queue.
- `USE_REDIS`: Set to `true` to enable Redis Streams backend.
- `WORKER_ID`: Unique name/id of the worker process.

### AI Credentials
- `GEMINI_API_KEY`: Google Gemini API key.
- `TAVILY_API_KEY`: Tavily search engine API key.
- `APOLLO_API_KEY`: Apollo prospect sourcing API key.

### Integrations
- `SENDGRID_API_KEY`: SendGrid SMTP email key.
- `GMAIL_CREDENTIALS_PATH`: Path to OAuth client `credentials.json`.
- `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD`: Direct LinkedIn login details (if utilizing Playwright).

### Telemetry
- `USE_OTEL`: Set to `true` to enable OpenTelemetry reporting.
- `OTEL_EXPORTER_OTLP_ENDPOINT`: Jaeger/Collector endpoint (e.g. `http://jaeger:4317`).
