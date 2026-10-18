"""Signature-verified SendGrid Inbound Parse reply processing.

The original body is parsed only in memory. It is never persisted or logged.
Replies are attributed through a signed Reply-To correlation token, never by
matching an arbitrary sender address to a lead.
"""
from __future__ import annotations

import datetime as dt
from email import policy
from email.parser import BytesParser, Parser
from email.utils import getaddresses
import hashlib
import json
from typing import Any

from gtm_engine.config import settings
from gtm_engine.crm import db
from gtm_engine.providers.sendgrid_webhooks import parse_reply_to_address


def _parse_multipart(raw_body: bytes, content_type: str) -> dict[str, str]:
    header = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + raw_body
    )
    message = BytesParser(policy=policy.default).parsebytes(header)
    if not message.is_multipart():
        raise ValueError("Malformed inbound multipart payload")
    fields: dict[str, str] = {}
    for part in message.iter_parts():
        disposition = part.get_content_disposition()
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if disposition != "form-data" or not name or filename:
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        fields[str(name).lower()] = payload.decode(charset, errors="replace")
    return fields


def _recipient_addresses(fields: dict[str, str]) -> list[str]:
    addresses: list[str] = []
    envelope_raw = fields.get("envelope", "")
    if envelope_raw:
        try:
            envelope = json.loads(envelope_raw)
            to_values = envelope.get("to", []) if isinstance(envelope, dict) else []
            if isinstance(to_values, str):
                to_values = [to_values]
            if isinstance(to_values, list):
                addresses.extend(str(value) for value in to_values)
        except (json.JSONDecodeError, TypeError):
            pass
    if fields.get("to"):
        addresses.extend(addr for _, addr in getaddresses([fields["to"]]) if addr)
        if "@" in fields["to"]:
            addresses.append(fields["to"].strip())
    return addresses


def _message_id(fields: dict[str, str]) -> str | None:
    raw_headers = fields.get("headers", "")
    if raw_headers:
        try:
            parsed = Parser(policy=policy.default).parsestr(raw_headers)
            value = parsed.get("Message-ID") or parsed.get("Message-Id")
            if value:
                return str(value).strip()
        except Exception:
            pass
    return (fields.get("message-id") or fields.get("message_id") or fields.get("Message-Id"))


def process_inbound_reply(raw_body: bytes, content_type: str) -> dict[str, Any]:
    """Parse and record one inbound reply safely and idempotently."""
    fields = _parse_multipart(raw_body, content_type)
    targets = parse_reply_to_address(_recipient_addresses(fields))
    message_id = (_message_id(fields) or "").strip()
    if message_id:
        event_identity = hashlib.sha256(("message-id:" + message_id).encode("utf-8")).hexdigest()
    else:
        # Fallback is deterministic for identical retries; Message-ID is preferred.
        event_identity = hashlib.sha256(raw_body).hexdigest()

    lead_id = outreach_id = decision_id = None
    if targets:
        lead_id, outreach_id, decision_id = targets
    identity_material = f"{event_identity}:{lead_id}:{outreach_id}:{decision_id}".encode("utf-8")
    identity_hash = hashlib.sha256(identity_material).hexdigest()
    receipt_result = db.create_or_get_provider_receipt(
        provider="sendgrid_inbound",
        provider_event_id=event_identity,
        provider_event_type="email_replied",
        payload_sha256=identity_hash,
        lead_id=lead_id,
        outreach_id=outreach_id,
        decision_id=decision_id,
    )
    receipt = receipt_result["receipt"]
    if receipt.get("payload_sha256") != identity_hash:
        return {"processed": 0, "ignored": 1, "duplicate": 0, "reason": "message_identity_conflict"}
    if receipt["status"] in {"processed", "ignored"}:
        return {"processed": 0, "ignored": 0, "duplicate": 1}
    if not targets:
        db.update_provider_receipt(receipt["id"], "ignored", "missing_or_invalid_reply_token")
        return {"processed": 0, "ignored": 1, "duplicate": 0}

    decision = db.get_decision(decision_id)
    outreach = next((item for item in db.all_outreach() if item["id"] == outreach_id), None)
    lead = db.get_lead(lead_id)
    if (not lead or not outreach or not decision
            or outreach.get("lead_id") != lead_id
            or decision.get("lead_id") != lead_id
            or decision.get("outreach_id") != outreach_id
            or outreach.get("channel") != "email"
            or outreach.get("sequence_step") != 0
            or outreach.get("sent_at") is None
            or outreach.get("status") not in {"sent", "replied"}):
        db.update_provider_receipt(receipt["id"], "ignored", "reply_token_attribution_rejected")
        return {"processed": 0, "ignored": 1, "duplicate": 0}

    try:
        occurred_at = dt.datetime.now(dt.timezone.utc)
        outcome = db.record_learning_outcome(
            lead_id=lead_id,
            outreach_id=outreach_id,
            decision_id=decision_id,
            event_type="email_replied",
            event_key=f"sendgrid-inbound:{event_identity}",
            occurred_at=occurred_at,
            source="sendgrid_inbound_parse",
            is_simulated=False,
            # Do not persist raw message body, sender, or recipient addresses.
            metadata={"provider": "sendgrid", "source": "inbound_parse", "message_identity_sha256": event_identity},
        )
        if outcome["status"] == "recorded":
            db.record_event(
                lead_id, "email_replied",
                f"provider=sendgrid_inbound;outreach_id={outreach_id};decision_id={decision_id}",
            )
        db.update_provider_receipt(receipt["id"], "processed")
        return {"processed": 1, "ignored": 0, "duplicate": 0}
    except ValueError as exc:
        if "not actually marked sent" in str(exc):
            db.update_provider_receipt(receipt["id"], "failed", "outreach_not_yet_marked_sent")
            raise RuntimeError("outreach send state is not committed yet") from exc
        db.update_provider_receipt(receipt["id"], "ignored", "reply_attribution_rejected")
        return {"processed": 0, "ignored": 1, "duplicate": 0}
    except Exception:
        db.update_provider_receipt(receipt["id"], "failed", "transient_processing_error")
        raise
