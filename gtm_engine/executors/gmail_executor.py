"""
Gmail Executor — real email delivery via the Gmail API (OAuth2).

Setup:
    1. Place credentials.json from Google Cloud Console in your project root
       (or set GMAIL_CREDENTIALS_PATH in .env)
    2. First real run opens a browser for OAuth consent → saves token.json
    3. Subsequent runs reuse the saved token (no browser needed)

Mock mode (GTM_MOCK_MODE=true):
    Does NOT call Gmail. Logs the email and returns a fake message ID.
"""
from __future__ import annotations

import base64
import email as email_lib
import logging
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any

log = logging.getLogger("gtm.gmail_executor")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _get_gmail_service():
    """Authenticate and return a Gmail API service object."""
    from gtm_engine.config import settings

    creds_path = settings.gmail_credentials_path or "credentials.json"
    token_path = "token.json"

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "Google auth libraries not installed. "
            "Run: pip install google-auth google-auth-oauthlib google-api-python-client"
        )

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"credentials.json not found at {creds_path}. "
                    "Download it from Google Cloud Console (OAuth 2.0 Client)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token:
            token.write(creds.to_json())
        log.info("GmailExecutor: saved OAuth token to %s", token_path)

    return build("gmail", "v1", credentials=creds)


def _build_message(to: str, subject: str, body: str, from_name: str = "") -> dict:
    """Build a Gmail API-ready message object."""
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["To"] = to
    if from_name:
        message["From"] = from_name
    message.attach(MIMEText(body, "plain"))
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return {"raw": raw}


def send_email(
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
    from_name: str | None = None,
    lead_id: int | None = None,
) -> dict[str, Any]:
    """
    Send an email via Gmail API.

    Returns:
        {"success": True, "message_id": "...", "thread_id": "..."}
        {"success": False, "error": "..."}
    """
    from gtm_engine.config import settings

    if settings.mock_mode:
        log.info("GmailExecutor [MOCK]: would send to=%s subject=%s", to, subject)
        return {
            "success": True,
            "message_id": f"mock-msg-{hash(to+subject) & 0xffff:04x}",
            "thread_id": thread_id or "mock-thread",
            "mock": True,
        }

    try:
        service = _get_gmail_service()
        msg = _build_message(to, subject, body, from_name or "")
        if thread_id:
            msg["threadId"] = thread_id

        result = service.users().messages().send(userId="me", body=msg).execute()
        log.info("GmailExecutor: sent to=%s msg_id=%s", to, result.get("id"))
        return {
            "success": True,
            "message_id": result.get("id"),
            "thread_id": result.get("threadId"),
        }
    except Exception as exc:
        log.error("GmailExecutor: send failed: %s", exc)
        return {"success": False, "error": str(exc)}


def list_replies(thread_id: str) -> list[dict]:
    """
    Fetch all messages in a Gmail thread.
    Returns list of {message_id, from, snippet, date}.
    """
    from gtm_engine.config import settings

    if settings.mock_mode:
        return []  # No real replies in mock mode

    try:
        service = _get_gmail_service()
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="metadata"
        ).execute()
        messages = thread.get("messages", [])
        result = []
        for msg in messages:
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            result.append({
                "message_id": msg["id"],
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            })
        return result
    except Exception as exc:
        log.error("GmailExecutor.list_replies: %s", exc)
        return []
