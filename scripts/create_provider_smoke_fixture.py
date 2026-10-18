"""Create one explicitly marked staging lead/outreach/decision for a real send test.

Required safeguards: GTM_ENVIRONMENT=staging, PostgreSQL, non-mock mode,
ENABLE_PROVIDER_SENDS=true, and exact test-recipient allowlisting.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid

from gtm_engine.config import settings
from gtm_engine.crm import db


def main() -> None:
    if os.getenv("GTM_ENVIRONMENT", "").lower() != "staging":
        raise SystemExit("Refusing to create a provider fixture unless GTM_ENVIRONMENT=staging")
    if settings.mock_mode:
        raise SystemExit("Set GTM_MOCK_MODE=false in the staging environment")
    if not settings.enable_provider_sends:
        raise SystemExit("Set ENABLE_PROVIDER_SENDS=true only in staging")
    if not settings.database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
        raise SystemExit("Refusing to create a staging fixture unless DATABASE_URL is PostgreSQL")
    recipient = os.getenv("PROVIDER_TEST_RECIPIENT", "").strip().lower()
    if not recipient or recipient not in settings.provider_test_allowed_recipients:
        raise SystemExit("PROVIDER_TEST_RECIPIENT must exactly match PROVIDER_TEST_ALLOWED_RECIPIENTS")
    if not settings.sendgrid_reply_to_domain or not settings.sendgrid_reply_token_secret:
        raise SystemExit("Configure SENDGRID_REPLY_TO_DOMAIN and SENDGRID_REPLY_TOKEN_SECRET first")
    if not settings.sendgrid_api_key or not settings.sendgrid_from_email:
        raise SystemExit("Configure SENDGRID_API_KEY and SENDGRID_FROM_EMAIL first")

    db.init_db()
    lead = next((row for row in db.all_leads()
                 if (row.get("email") or "").lower() == recipient
                 and row.get("company") == "GTM Engine Staging Validation"), None)
    if lead:
        candidate = next((row for row in db.all_outreach()
                          if row.get("lead_id") == lead["id"]
                          and row.get("subject") == "GTM Engine V5 staging validation"
                          and row.get("status") in {"drafted", "scheduled"}
                          and row.get("sent_at") is None), None)
        if candidate:
            decision = db.get_decision_for_outreach(candidate["id"])
            if decision:
                print(json.dumps({"status": "existing_unsent_fixture", "lead_id": lead["id"],
                                  "outreach_id": candidate["id"], "decision_id": decision["id"]}, indent=2))
                return

    now = dt.datetime.now(dt.timezone.utc)
    run_id = f"provider-smoke-{uuid.uuid4()}"
    lead_id = db.insert_lead({
        "full_name": "GTM Staging Test", "title": "Staging Integration Test",
        "company": "GTM Engine Staging Validation", "domain": recipient.rsplit("@", 1)[-1],
        "email": recipient,
        "raw": {"staging_fixture": True, "purpose": "single-provider-webhook-validation"},
    })
    outreach_id = db.save_outreach(
        lead_id=lead_id, channel="email", step=0,
        subject="GTM Engine V5 staging validation",
        body=("This is a controlled staging integration test for the Autonomous GTM Engine. "
              "No action is required. Please reply to this message with 'staging reply verified'."),
        scheduled_for=now, status="drafted", variant="A",
    )
    decision_id = db.record_decision(
        lead_id=lead_id, action="A", action_value="direct", assignment_probability=0.5,
        policy_name="provider_staging_smoke_v1", decision_key=f"{run_id}:{outreach_id}",
        outreach_id=outreach_id, campaign_run_id=run_id, channel="email", sequence_step=0,
        decision_type="email_style", segment_key="staging", context={"staging_fixture": True},
        created_at=now,
    )
    print(json.dumps({"status": "created", "lead_id": lead_id, "outreach_id": outreach_id,
                      "decision_id": decision_id, "recipient": recipient,
                      "subject": "GTM Engine V5 staging validation"}, indent=2))


if __name__ == "__main__":
    main()
