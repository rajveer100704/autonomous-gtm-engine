"""Fast unit/API tests for verified SendGrid event ingestion."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from gtm_engine import api
from gtm_engine.config import settings
from gtm_engine.crm import db

UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db.recreate_db_engine(f"sqlite:///{tmp_path / 'sendgrid_webhooks.db'}")
    db.init_db()
    yield
    db.engine.dispose()


def make_exposure():
    now = dt.datetime.now(UTC) - dt.timedelta(days=30)
    lead_id = db.insert_lead({
        "full_name": "Staging Test", "title": "CTO", "company": "Example",
        "domain": "example.test", "email": "owned-test@example.test", "raw": {},
    })
    outreach_id = db.save_outreach(
        lead_id, "email", 0, "Staging validation", "Test body", now, "drafted", "A"
    )
    db.update_outreach_status(outreach_id, "sent", sent_at=now)
    decision_id = db.record_decision(
        lead_id=lead_id, action="A", action_value="direct", assignment_probability=0.5,
        policy_name="randomized_baseline_v1", decision_key=f"test-decision-{outreach_id}",
        outreach_id=outreach_id, channel="email", sequence_step=0,
        decision_type="email_style", segment_key="all",
    )
    return lead_id, outreach_id, decision_id, now


def event(event_id, event_type, lead_id, outreach_id, decision_id, occurred_at):
    return {
        "sg_event_id": event_id,
        "event": event_type,
        "timestamp": int(occurred_at.timestamp()),
        "lead_id": str(lead_id),
        "outreach_id": str(outreach_id),
        "decision_id": str(decision_id),
    }


def test_signed_event_processor_is_idempotent_and_attributes_exactly():
    lead, outreach, decision, sent_at = make_exposure()
    from gtm_engine.providers.sendgrid_webhooks import process_events
    payload = [event("sg-open-1", "open", lead, outreach, decision, sent_at + dt.timedelta(days=1))]

    first = process_events(payload)
    second = process_events(payload)

    assert first["processed"] == 1
    assert second["duplicates"] == 1
    outcomes = db.all_learning_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["source"] == "sendgrid_webhook"
    assert outcomes[0]["is_simulated"] == 0
    receipts = db.list_provider_receipts()
    assert len(receipts) == 1
    assert receipts[0]["status"] == "processed"


def test_missing_correlation_ids_are_not_learned_from():
    from gtm_engine.providers.sendgrid_webhooks import process_events
    summary = process_events([{"sg_event_id": "no-correlation", "event": "open", "timestamp": 1}])
    assert summary["ignored"] == 1
    assert db.all_learning_outcomes() == []
    assert db.list_provider_receipts()[0]["error_code"] == "missing_correlation_ids"


def test_delivery_is_not_misclassified_as_reply():
    lead, outreach, decision, sent_at = make_exposure()
    from gtm_engine.providers.sendgrid_webhooks import process_events
    summary = process_events([event("sg-delivered-1", "delivered", lead, outreach, decision, sent_at)])
    assert summary["processed"] == 1
    assert db.all_learning_outcomes() == []


def test_webhook_rejects_missing_or_invalid_signature(monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_webhook_public_key", "")
    client = TestClient(api.app)
    response = client.post("/webhooks/sendgrid/events", json=[])
    assert response.status_code == 403
    assert db.all_learning_outcomes() == []


def test_webhook_accepts_only_verified_raw_request(monkeypatch):
    import gtm_engine.providers.sendgrid_webhooks as webhooks
    monkeypatch.setattr(webhooks, "verify_signature", lambda payload, signature, timestamp: payload == b"[]" and signature == "ok" and timestamp == "123")
    client = TestClient(api.app)
    response = client.post(
        "/webhooks/sendgrid/events", content=b"[]",
        headers={"Content-Type": "application/json", webhooks.SIGNATURE_HEADER: "ok", webhooks.TIMESTAMP_HEADER: "123"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_internal_event_route_requires_token_outside_mock_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "internal_event_token", "test-secret")
    client = TestClient(api.app)
    response = client.post("/events/track", json={"lead_id": 1, "event_type": "email_opened"})
    assert response.status_code == 403
    accepted = client.post(
        "/events/track", headers={"X-GTM-Event-Token": "test-secret"},
        json={"lead_id": 1, "event_type": "email_opened"},
    )
    assert accepted.status_code == 404  # authenticated, then correctly rejects unknown lead


def test_one_message_provider_smoke_send_requires_auth_allowlist_and_exact_decision(monkeypatch):
    monkeypatch.setenv("GTM_ENVIRONMENT", "staging")
    lead, outreach, decision, _ = make_exposure()
    # Reset status to draft to model a staging message that has not been sent.
    db.update_outreach_status(outreach, "drafted")
    with db.engine.begin() as conn:
        conn.execute(db.outreach.update().where(db.outreach.c.id == outreach).values(sent_at=None))
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "enable_provider_sends", True)
    monkeypatch.setattr(settings, "provider_send_auth_token", "staging-token")
    monkeypatch.setattr(settings, "provider_test_allowed_recipients", ("owned-test@example.test",))
    monkeypatch.setattr(settings, "sendgrid_reply_to_domain", "parse.example.test")
    monkeypatch.setattr(settings, "sendgrid_reply_token_secret", "test-reply-secret")
    monkeypatch.setattr(settings, "sendgrid_api_key", "test-api-key")
    monkeypatch.setattr(settings, "sendgrid_from_email", "staging@example.test")
    from gtm_engine.executors import sendgrid_executor
    monkeypatch.setattr(sendgrid_executor, "send_email", lambda *args, **kwargs: {
        "success": True, "mock": False, "message_id": "provider-message-id", "provider_status": 202,
    })
    client = TestClient(api.app)
    response = client.post("/provider/send-test", json={"outreach_id": outreach, "decision_id": decision})
    assert response.status_code == 403
    response = client.post(
        "/provider/send-test", headers={"Authorization": "Bearer staging-token"},
        json={"outreach_id": outreach, "decision_id": decision},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "provider_accepted"
    from gtm_engine.providers.sendgrid_webhooks import make_reply_to_address
    sent_call_address = make_reply_to_address(lead, outreach, decision)
    assert sent_call_address.startswith("reply+v1.") and sent_call_address.endswith("@parse.example.test")
    sent = next(item for item in db.all_outreach() if item["id"] == outreach)
    assert sent["status"] == "sent"
    assert sent["sent_at"] is not None
    # A second request cannot send the same outreach a second time.
    again = client.post(
        "/provider/send-test", headers={"Authorization": "Bearer staging-token"},
        json={"outreach_id": outreach, "decision_id": decision},
    )
    assert again.status_code == 409



def make_inbound_multipart(reply_to, message_id="<reply-1@example.test>"):
    boundary = "gtm-test-boundary"
    fields = {
        "envelope": '{"to": ["' + reply_to + '"], "from": "sender@example.test"}',
        "to": reply_to,
        "from": "sender@example.test",
        "subject": "Re: staging validation",
        "headers": "Message-ID: " + message_id + "\r\nIn-Reply-To: <sent-message@example.test>\r\n",
        "text": "Thanks, please send more information. This should not be stored.",
    }
    chunks = []
    for name, value in fields.items():
        chunks.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode())
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def test_signed_inbound_reply_is_exactly_attributed_and_idempotent(monkeypatch):
    from gtm_engine.providers.sendgrid_inbound import process_inbound_reply
    from gtm_engine.providers.sendgrid_webhooks import make_reply_to_address
    lead, outreach, decision, _ = make_exposure()
    monkeypatch.setattr(settings, "sendgrid_reply_to_domain", "parse.example.test")
    monkeypatch.setattr(settings, "sendgrid_reply_token_secret", "reply-secret")
    address = make_reply_to_address(lead, outreach, decision)
    raw, content_type = make_inbound_multipart(address)

    first = process_inbound_reply(raw, content_type)
    second = process_inbound_reply(raw, content_type)

    assert first["processed"] == 1
    assert second["duplicate"] == 1
    outcomes = db.all_learning_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["event_type"] == "email_replied"
    assert outcomes[0]["source"] == "sendgrid_inbound_parse"
    assert "Thanks, please send more information" not in str(outcomes[0].get("metadata_json"))
    assert db.list_provider_receipts("sendgrid_inbound")[0]["status"] == "processed"


def test_inbound_reply_with_tampered_reply_token_is_never_learned(monkeypatch):
    from gtm_engine.providers.sendgrid_inbound import process_inbound_reply
    from gtm_engine.providers.sendgrid_webhooks import make_reply_to_address
    lead, outreach, decision, _ = make_exposure()
    monkeypatch.setattr(settings, "sendgrid_reply_to_domain", "parse.example.test")
    monkeypatch.setattr(settings, "sendgrid_reply_token_secret", "reply-secret")
    address = make_reply_to_address(lead, outreach, decision)
    tampered = address.replace("reply+v1.", "reply+v1.9", 1)
    raw, content_type = make_inbound_multipart(tampered)
    result = process_inbound_reply(raw, content_type)
    assert result["ignored"] == 1
    assert db.all_learning_outcomes() == []


def test_inbound_route_verifies_signature_before_processing(monkeypatch):
    import gtm_engine.providers.sendgrid_webhooks as event_webhooks
    monkeypatch.setattr(settings, "sendgrid_inbound_webhook_public_key", "inbound-test-key")
    monkeypatch.setattr(settings, "sendgrid_reply_to_domain", "parse.example.test")
    monkeypatch.setattr(settings, "sendgrid_reply_token_secret", "reply-secret")
    monkeypatch.setattr(event_webhooks, "verify_signature", lambda payload, signature, timestamp, public_key_override=None: (
        payload.startswith(b"--gtm-test-boundary") and signature == "ok" and timestamp == "123"
        and public_key_override == "inbound-test-key"
    ))
    lead, outreach, decision, _ = make_exposure()
    from gtm_engine.providers.sendgrid_webhooks import make_reply_to_address
    address = make_reply_to_address(lead, outreach, decision)
    raw, content_type = make_inbound_multipart(address)
    client = TestClient(api.app)
    response = client.post(
        "/webhooks/sendgrid/inbound", content=raw,
        headers={"Content-Type": content_type,
                 "X-Twilio-Email-Event-Webhook-Signature": "ok",
                 "X-Twilio-Email-Event-Webhook-Timestamp": "123"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["processed"] == 1


def test_sendgrid_sender_uses_only_non_pii_correlation_custom_args(monkeypatch):
    from types import SimpleNamespace
    from gtm_engine.executors import sendgrid_executor
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "sendgrid_api_key", "test-api-key")
    monkeypatch.setattr(settings, "sendgrid_from_email", "staging@example.test")
    captured = {}
    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return SimpleNamespace(status_code=202, headers={"X-Message-Id": "msg-123"})
    monkeypatch.setattr(sendgrid_executor.requests, "post", fake_post)
    result = sendgrid_executor.send_email(
        "controlled@example.test", "Staging", "Test body",
        custom_args={"lead_id": 1, "outreach_id": 2, "decision_id": 3,
                     "campaign_run_id": "run-4", "email": "do-not-forward@example.test"},
        reply_to="reply+v1.test@parse.example.test",
    )
    assert result["success"] is True
    assert result["message_id"] == "msg-123"
    assert captured["url"].endswith("/v3/mail/send")
    personalization = captured["json"]["personalizations"][0]
    assert personalization["custom_args"] == {
        "lead_id": "1", "outreach_id": "2", "decision_id": "3", "campaign_run_id": "run-4"
    }
    assert captured["json"]["reply_to"]["email"] == "reply+v1.test@parse.example.test"
    assert "do-not-forward@example.test" not in str(captured["json"])


def test_crypto_verifier_checks_exact_raw_bytes_including_multipart_bytes(monkeypatch):
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from gtm_engine.providers.sendgrid_webhooks import verify_signature

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_key = base64.b64encode(public_der).decode("ascii")
    monkeypatch.setattr(settings, "sendgrid_webhook_public_key", public_key)
    timestamp = "1792400000"
    # Include CRLF and a non-UTF8 byte to ensure signature checks use original bytes.
    payload = b"--boundary\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\nreply\xff\r\n--boundary--\r\n"
    signature = base64.b64encode(
        private_key.sign(timestamp.encode("utf-8") + payload, ec.ECDSA(hashes.SHA256()))
    ).decode("ascii")
    assert verify_signature(payload, signature, timestamp) is True
    assert verify_signature(payload + b"tampered", signature, timestamp) is False


def test_provider_smoke_send_is_forbidden_outside_staging(monkeypatch):
    monkeypatch.setenv("GTM_ENVIRONMENT", "production")
    client = TestClient(api.app)
    response = client.post("/provider/send-test", json={"outreach_id": 1, "decision_id": 1})
    assert response.status_code == 403
    assert response.json()["detail"] == "Provider smoke sends are staging-only"


def test_readiness_reports_environment(monkeypatch):
    monkeypatch.setenv("GTM_ENVIRONMENT", "staging")
    client = TestClient(api.app)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["environment"] == "staging"
