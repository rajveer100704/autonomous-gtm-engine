"""Authenticated SendGrid Event Webhook verification and idempotent ingestion."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import re
from typing import Any

from gtm_engine.config import settings
from gtm_engine.crm import db

SIGNATURE_HEADER = "X-Twilio-Email-Event-Webhook-Signature"
TIMESTAMP_HEADER = "X-Twilio-Email-Event-Webhook-Timestamp"

# Only events that already exist in the V5 learning vocabulary enter learning.
# SendGrid's delivery/open events are not treated as replies.
EVENT_MAP = {
    "open": "email_opened",
    "bounce": "bounced",
    "dropped": "bounced",
    "unsubscribe": "unsubscribe",
    "group_unsubscribe": "unsubscribe",
    "spamreport": "unsubscribe",
}


def verify_signature(
    payload: bytes, signature: str | None, timestamp: str | None, public_key_override: str | None = None,
) -> bool:
    """Verify exact raw request bytes using SendGrid's official verifier; fail closed."""
    public_key = (public_key_override if public_key_override is not None
                  else settings.sendgrid_webhook_public_key).strip()
    if not public_key or not signature or not timestamp:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        # SendGrid publishes a base64-encoded DER SubjectPublicKeyInfo. Verify
        # timestamp + the exact raw request bytes (including MIME boundaries/CRLF).
        key_der = base64.b64decode(public_key, validate=True)
        public_key_obj = serialization.load_der_public_key(key_der)
        if not isinstance(public_key_obj, ec.EllipticCurvePublicKey):
            return False
        signature_bytes = base64.b64decode(signature, validate=True)
        public_key_obj.verify(
            signature_bytes, timestamp.encode("utf-8") + payload, ec.ECDSA(hashes.SHA256())
        )
        return True
    except InvalidSignature:
        return False
    except Exception:
        # Invalid keys/signatures and missing crypto dependencies fail closed.
        return False


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _correlation(event: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    custom_args = event.get("custom_args")
    if not isinstance(custom_args, dict):
        custom_args = {}
    merged = {**custom_args, **event}
    return (
        _int_or_none(merged.get("lead_id")),
        _int_or_none(merged.get("outreach_id")),
        _int_or_none(merged.get("decision_id")),
    )


def _occurred_at(event: dict[str, Any]) -> dt.datetime | None:
    try:
        timestamp = float(event.get("timestamp"))
        return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def process_events(events: list[dict[str, Any]]) -> dict[str, int]:
    """Persist authenticated event receipts and apply only valid exact attribution.

    Pending/failed receipts are resumed on retry. Outcome event keys and provider
    receipt uniqueness prevent retry/concurrency double-counting. Raw payloads
    and recipient addresses are not persisted.
    """
    summary = {"received": len(events), "processed": 0, "ignored": 0, "duplicates": 0}
    for event in events:
        if not isinstance(event, dict):
            summary["ignored"] += 1
            continue
        provider_event_type = str(event.get("event", "unknown")).lower()
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        provider_event_id = str(event.get("sg_event_id") or payload_hash)
        lead_id, outreach_id, decision_id = _correlation(event)

        receipt_result = db.create_or_get_provider_receipt(
            provider="sendgrid", provider_event_id=provider_event_id,
            provider_event_type=provider_event_type, payload_sha256=payload_hash,
            lead_id=lead_id, outreach_id=outreach_id, decision_id=decision_id,
        )
        receipt = receipt_result["receipt"]
        if receipt.get("payload_sha256") != payload_hash:
            # Same provider event ID with different signed content is not a retry.
            summary["ignored"] += 1
            continue
        if receipt["status"] in {"processed", "ignored"}:
            summary["duplicates"] += 1
            continue

        learning_event = EVENT_MAP.get(provider_event_type)
        if learning_event is None:
            db.update_provider_receipt(receipt["id"], "processed")
            summary["processed"] += 1
            continue
        if lead_id is None or outreach_id is None or decision_id is None:
            db.update_provider_receipt(receipt["id"], "ignored", "missing_correlation_ids")
            summary["ignored"] += 1
            continue

        try:
            outcome = db.record_learning_outcome(
                lead_id=lead_id,
                outreach_id=outreach_id,
                decision_id=decision_id,
                event_type=learning_event,
                event_key=f"sendgrid:{provider_event_id}",
                occurred_at=_occurred_at(event),
                source="sendgrid_webhook",
                is_simulated=False,
                metadata={"provider": "sendgrid", "provider_event_type": provider_event_type,
                         "provider_event_id_sha256": hashlib.sha256(provider_event_id.encode()).hexdigest()},
            )
            if outcome["status"] == "recorded":
                db.record_event(
                    lead_id, learning_event,
                    f"provider=sendgrid;outreach_id={outreach_id};decision_id={decision_id}",
                )
            db.update_provider_receipt(receipt["id"], "processed")
            summary["processed"] += 1
        except ValueError as exc:
            # The provider may callback before the sending request commits sent_at.
            # Treat that specific race as retryable; do not permanently discard it.
            if "not actually marked sent" in str(exc):
                db.update_provider_receipt(receipt["id"], "failed", "outreach_not_yet_marked_sent")
                raise RuntimeError("outreach send state is not committed yet") from exc
            # Other authenticated but un-attributable events are retained as receipts,
            # never learned from. Returning 2xx avoids futile retries of invalid links.
            db.update_provider_receipt(receipt["id"], "ignored", "attribution_rejected")
            summary["ignored"] += 1
        except Exception:
            db.update_provider_receipt(receipt["id"], "failed", "transient_processing_error")
            # 500 lets the provider retry; the durable receipt can be resumed.
            raise
    return summary


_REPLY_LOCAL_RE = re.compile(
    r"^reply\+v1\.(\d+)\.(\d+)\.(\d+)\.([0-9a-f]{64})$", re.IGNORECASE
)


def make_reply_to_address(lead_id: int, outreach_id: int, decision_id: int) -> str:
    """Create a signed, opaque correlation address for SendGrid Inbound Parse."""
    domain = settings.sendgrid_reply_to_domain.strip().lower().strip(".")
    secret = settings.sendgrid_reply_token_secret
    if not domain or not secret:
        raise ValueError("SendGrid reply domain and token secret must be configured")
    payload = f"v1:{int(lead_id)}:{int(outreach_id)}:{int(decision_id)}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"reply+v1.{int(lead_id)}.{int(outreach_id)}.{int(decision_id)}.{signature}@{domain}"


def parse_reply_to_address(addresses: list[str]) -> tuple[int, int, int] | None:
    """Validate the signed correlation token from a parsed Inbound Parse recipient."""
    domain = settings.sendgrid_reply_to_domain.strip().lower().strip(".")
    secret = settings.sendgrid_reply_token_secret
    if not domain or not secret:
        return None
    for address in addresses:
        clean = address.strip().strip("<>").lower()
        if "@" not in clean:
            continue
        local, found_domain = clean.rsplit("@", 1)
        if found_domain != domain:
            continue
        match = _REPLY_LOCAL_RE.fullmatch(local)
        if not match:
            continue
        lead_id, outreach_id, decision_id = (int(match.group(i)) for i in (1, 2, 3))
        supplied = match.group(4).lower()
        payload = f"v1:{lead_id}:{outreach_id}:{decision_id}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        if hmac.compare_digest(supplied, expected):
            return lead_id, outreach_id, decision_id
    return None
