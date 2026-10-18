# V5 Production Validation Runbook

This runbook separates automated verification from real provider checks. Do not mark a live check complete based on unit tests, a fixture, or the synthetic simulator.

## What is implemented

- `POST /webhooks/sendgrid/events`: verifies the raw body using the SendGrid Event Webhook signature and stores durable, deduplicated event receipts. Authenticated `open`, `bounce`, `dropped`, `unsubscribe`, `group_unsubscribe`, and `spamreport` events can enter V5 learning after exact lead/outreach/decision attribution. A `delivered` event is **not** interpreted as a reply.
- `POST /webhooks/sendgrid/inbound`: verifies the raw multipart request with the separate SendGrid Inbound Parse public key. It accepts replies only when the Reply-To correlation token is HMAC-valid and matches an actually sent first-touch email and its learning decision. Message bodies and sender/recipient addresses are not copied into learning metadata.
- `POST /provider/send-test`: a one-message staging-only SendGrid sender, disabled unless `GTM_MOCK_MODE=false`, `ENABLE_PROVIDER_SENDS=true`, an auth token, an exact recipient allowlist, and signed reply routing are configured. It marks the outreach `sending` before the network request. If the process is interrupted in that state, reconcile the provider message before any manual retry; this is an intentional at-most-once safety guard.
- `GET /health/ready`: readiness probe that opens a database connection and executes `SELECT 1`.
- `provider_event_receipts`: additive inbox table with a unique provider/event key and resumable processing statuses. Existing tables/data are not dropped by `db.init_db()`.

## 1. Run SQLite/unit checks

From the repository root, with the project's Python environment activated:

```powershell
python -m compileall -q gtm_engine
$env:GTM_MOCK_MODE = "true"
$env:USE_REDIS = "false"
$env:USE_OTEL = "false"
python -m pytest -q
```

## 2. Run the PostgreSQL integration suite locally

Requires Docker Desktop running. The test suite drops and recreates tables, so **use only this disposable test container/database**.

```powershell
docker run --rm -d --name gtm-pg-v5-test `
  -e POSTGRES_USER=gtm_test `
  -e POSTGRES_PASSWORD=gtm_test `
  -e POSTGRES_DB=gtm_test `
  -p 55432:5432 postgres:16-alpine

try {
  do {
    Start-Sleep -Seconds 2
    docker exec gtm-pg-v5-test pg_isready -U gtm_test -d gtm_test
    $ready = $LASTEXITCODE -eq 0
  } until ($ready)

  $env:TEST_DATABASE_URL = "postgresql+psycopg2://gtm_test:gtm_test@localhost:55432/gtm_test"
  python -m pytest -v --tb=short gtm_engine/tests/postgres_integration.py
  if ($LASTEXITCODE -ne 0) { throw "PostgreSQL integration tests failed" }
}
finally {
  docker rm -f gtm-pg-v5-test 2>$null | Out-Null
  Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
}
```

The integration test file fails (rather than skips) when `TEST_DATABASE_URL` is missing or is not PostgreSQL. CI runs it against a PostgreSQL 16 service container.

## 3. Configure SendGrid staging credentials

Use a dedicated staging SendGrid key with only the permissions needed for sending and webhook configuration. Configure these environment variables in the **staging** secret store, not in source control:

```text
GTM_ENVIRONMENT=staging
GTM_MOCK_MODE=false
ENABLE_PROVIDER_SENDS=true
SENDGRID_API_KEY=<staging API key>
SENDGRID_FROM_EMAIL=<verified sender address>
SENDGRID_WEBHOOK_PUBLIC_KEY=<Event Webhook signature verification public key>
SENDGRID_INBOUND_WEBHOOK_PUBLIC_KEY=<Inbound Parse security-policy public key>
SENDGRID_REPLY_TO_DOMAIN=<dedicated authenticated inbound subdomain>
SENDGRID_REPLY_TOKEN_SECRET=<random 32+ byte secret generated for this purpose>
PROVIDER_SEND_AUTH_TOKEN=<long random API token>
PROVIDER_TEST_ALLOWED_RECIPIENTS=<exact mailbox you control>
GTM_INTERNAL_EVENT_TOKEN=<separate random token for trusted internal event calls>
DATABASE_URL=<staging PostgreSQL URL>
```

Generate independent secrets locally if needed:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Do not reuse `SENDGRID_API_KEY` as a webhook key or token. Do not put secrets in shell command history, source files, reports, screenshots, or Git commits. Use the provider's published verification public keys for their respective hooks.

### Event Webhook

In SendGrid, configure the Event Webhook URL as `https://<staging-host>/webhooks/sendgrid/events`; enable signature verification and copy its verification public key to `SENDGRID_WEBHOOK_PUBLIC_KEY`. Select the events you intend to validate. Webhook signature verification must stay enabled.

### Inbound Parse for actual replies

Configure a dedicated receiving subdomain in SendGrid Inbound Parse, create its required MX record pointing to SendGrid, and attach a security policy with signature verification. Set the destination URL to `https://<staging-host>/webhooks/sendgrid/inbound` and copy that policy's public key separately to `SENDGRID_INBOUND_WEBHOOK_PUBLIC_KEY`. Do not use an unauthenticated public inbound route. A DNS owner/admin may be needed for the MX record.

SendGrid's setup/security references:

- Event Webhook signed requests: <https://www.twilio.com/docs/sendgrid/for-developers/tracking-events/getting-started-event-webhook-security-features>
- Inbound Parse webhook security: <https://www.twilio.com/docs/sendgrid/for-developers/parsing-email/securing-your-parse-webhooks>
- Inbound Parse/DNS configuration: <https://www.twilio.com/docs/sendgrid/for-developers/parsing-email/setting-up-the-inbound-parse-webhook>

## 4. Create and send one controlled staging message

Once the staging service has the values above and uses the staging PostgreSQL database:

```powershell
$env:PROVIDER_TEST_RECIPIENT = "<the same exact mailbox in PROVIDER_TEST_ALLOWED_RECIPIENTS>"
python scripts/create_provider_smoke_fixture.py
```

The script prints `lead_id`, `outreach_id`, and `decision_id`. Use those IDs once to call the protected endpoint:

```powershell
$body = @{ outreach_id = <OUTREACH_ID>; decision_id = <DECISION_ID> } | ConvertTo-Json
$secureToken = Read-Host "Provider send auth token" -AsSecureString
$token = [System.Net.NetworkCredential]::new("", $secureToken).Password
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Method Post `
  -Uri "https://<staging-host>/provider/send-test" `
  -Headers $headers -ContentType "application/json" -Body $body
$token = $null
$secureToken = $null
```

Replace the placeholders locally; do not commit this command or its token. The endpoint sends only to the exact allowlisted recipient and will reject a second send for the same outreach.

Confirm all of the following:

1. `GET https://<staging-host>/health/ready` returns `status=ready` and reports PostgreSQL.
2. The provider accepted the single test email.
3. An actual signed SendGrid event callback reaches `/webhooks/sendgrid/events`; a duplicate retry creates no second learning outcome.
4. Missing/tampered signatures are rejected with HTTP 403 and create no outcome.
5. Reply to the delivered message from the controlled mailbox. The signed Inbound Parse callback reaches `/webhooks/sendgrid/inbound` and records exactly one `email_replied` outcome linked to the same lead/outreach/decision IDs.
6. The callback logs and receipt metadata contain no raw message body, provider key, signature or unneeded addresses.

Do not manually resend if the outreach remains `sending` after a timeout. Check SendGrid activity and the staging database first. Clear/reconcile that state only after confirming whether the provider accepted the message.

## 5. CI and evidence

GitHub Actions has a separate `postgres-integration` job with PostgreSQL 16; it requires the test file to execute instead of silently skipping. After pushing, confirm that job and the normal test/lint/Compose/Docker jobs pass. Record the commit SHA, workflow run URL, PostgreSQL version, staging test timestamps (UTC), redacted receipt IDs/statuses and observed counts.

Keep the benchmark explicitly marked **synthetic**. Passing provider and PostgreSQL tests establishes integration behavior, not real campaign uplift. Real-world improvements require a separate controlled campaign experiment with mature outcomes and the V5 evidence/promotion gates.
