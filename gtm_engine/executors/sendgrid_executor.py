"""Small SendGrid REST adapter with no automatic live-send fallback.

The caller must explicitly authorize sending. This module returns a small,
redacted result and never logs recipient addresses, bodies, or credentials.
"""
from __future__ import annotations

from typing import Any
import requests


def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    custom_args: dict[str, str | int] | None = None,
    from_email: str | None = None,
    reply_to: str | None = None,
) -> dict[str, Any]:
    from gtm_engine.config import settings

    if settings.mock_mode:
        return {"success": True, "mock": True, "message_id": None}
    if not settings.sendgrid_api_key:
        return {"success": False, "error_code": "sendgrid_api_key_missing"}
    sender = from_email or settings.sendgrid_from_email
    if not sender:
        return {"success": False, "error_code": "sendgrid_from_email_missing"}
    if not to or "@" not in to:
        return {"success": False, "error_code": "invalid_recipient"}

    safe_args = {
        str(key): str(value)
        for key, value in (custom_args or {}).items()
        if str(key) in {"lead_id", "outreach_id", "decision_id", "campaign_run_id"}
        and value is not None
    }
    payload = {
        "personalizations": [{"to": [{"email": to}], "custom_args": safe_args}],
        "from": {"email": sender},
        "subject": subject or "",
        "content": [{"type": "text/plain", "value": body or ""}],
    }
    if reply_to:
        payload["reply_to"] = {"email": reply_to}
    try:
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {settings.sendgrid_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(5, 20),
        )
    except requests.RequestException:
        return {"success": False, "error_code": "sendgrid_request_failed"}

    if response.status_code == 202:
        return {
            "success": True,
            "mock": False,
            "message_id": response.headers.get("X-Message-Id"),
            "provider_status": 202,
        }
    # Do not forward arbitrary provider response text, which may contain PII.
    return {"success": False, "error_code": "sendgrid_rejected", "provider_status": response.status_code}
