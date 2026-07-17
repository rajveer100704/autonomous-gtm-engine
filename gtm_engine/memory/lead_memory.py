"""
Lead Memory — persists per-lead interaction history so every campaign starts
with context about what was tried, what was said, and what happened.

Storage: SQLite/Postgres via a dedicated 'lead_memory' table (added to db.py).
Fallback: if the table doesn't exist yet, returns safe empty defaults.

Usage:
    from gtm_engine.memory.lead_memory import lead_memory

    # Load context before generating
    ctx = lead_memory.load(lead_id)

    # Update after an interaction
    lead_memory.update(lead_id,
        last_email_subject="We noticed your hiring spike",
        objections=["Already using Clay", "No budget until Q4"],
        reply_classification="not_now",
    )
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any

log = logging.getLogger("gtm.lead_memory")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class LeadMemory:
    """
    Thin wrapper around a 'lead_memory' SQL table.
    Falls back gracefully if the table hasn't been created yet.
    """

    def _db(self):
        from gtm_engine.crm import db
        return db

    def _engine(self):
        return self._db().engine

    def _ensure_table(self) -> None:
        """Create the lead_memory table if it doesn't exist (idempotent)."""
        from sqlalchemy import (
            Table, Column, Integer, Text, DateTime, MetaData,
            inspect as sa_inspect
        )
        engine = self._engine()
        insp = sa_inspect(engine)
        if "lead_memory" not in insp.get_table_names():
            meta = MetaData()
            tbl = Table(
                "lead_memory", meta,
                Column("id", Integer, primary_key=True, autoincrement=True),
                Column("lead_id", Integer, unique=True),
                Column("past_emails_json", Text, default="[]"),
                Column("past_replies_json", Text, default="[]"),
                Column("objections_json", Text, default="[]"),
                Column("last_email_subject", Text, nullable=True),
                Column("last_reply_classification", Text, nullable=True),
                Column("last_interaction_at", DateTime, nullable=True),
                Column("notes", Text, nullable=True),
                Column("updated_at", DateTime),
            )
            meta.create_all(engine)
            log.info("Created lead_memory table")

    def load(self, lead_id: int) -> dict:
        """Return the memory record for a lead. Returns safe defaults if none."""
        try:
            self._ensure_table()
            from sqlalchemy import text
            with self._engine().connect() as conn:
                row = conn.execute(
                    text("SELECT * FROM lead_memory WHERE lead_id = :lid"),
                    {"lid": lead_id},
                ).mappings().fetchone()
            if not row:
                return self._empty()
            return {
                "lead_id": lead_id,
                "past_emails": json.loads(row.get("past_emails_json") or "[]"),
                "past_replies": json.loads(row.get("past_replies_json") or "[]"),
                "objections": json.loads(row.get("objections_json") or "[]"),
                "last_email_subject": row.get("last_email_subject"),
                "last_reply_classification": row.get("last_reply_classification"),
                "last_interaction_at": str(row.get("last_interaction_at") or ""),
                "notes": row.get("notes") or "",
            }
        except Exception as exc:
            log.warning("LeadMemory.load failed: %s", exc)
            return self._empty()

    def update(self, lead_id: int, **kwargs) -> None:
        """
        Upsert memory fields. Supported kwargs:
            past_emails: list[str] — append-only, pass new items
            past_replies: list[str] — append-only
            objections: list[str] — append-only (deduped)
            last_email_subject: str
            last_reply_classification: str
            notes: str
        """
        try:
            self._ensure_table()
            existing = self.load(lead_id)

            # Merge lists (append-only)
            emails = existing["past_emails"]
            if "past_emails" in kwargs:
                emails = (emails + kwargs["past_emails"])[-20:]  # cap at 20

            replies = existing["past_replies"]
            if "past_replies" in kwargs:
                replies = (replies + kwargs["past_replies"])[-20:]

            objections = list(set(existing["objections"] + kwargs.get("objections", [])))

            record = {
                "lead_id": lead_id,
                "past_emails_json": json.dumps(emails),
                "past_replies_json": json.dumps(replies),
                "objections_json": json.dumps(objections),
                "last_email_subject": kwargs.get("last_email_subject", existing["last_email_subject"]),
                "last_reply_classification": kwargs.get("last_reply_classification", existing["last_reply_classification"]),
                "notes": kwargs.get("notes", existing["notes"]),
                "updated_at": datetime.datetime.now(datetime.timezone.utc),
                "last_interaction_at": datetime.datetime.now(datetime.timezone.utc),
            }

            from sqlalchemy import text
            with self._engine().begin() as conn:
                # SQLite-compatible upsert
                existing_check = conn.execute(
                    text("SELECT id FROM lead_memory WHERE lead_id = :lid"),
                    {"lid": lead_id},
                ).fetchone()

                if existing_check:
                    conn.execute(
                        text("""UPDATE lead_memory SET
                            past_emails_json=:past_emails_json,
                            past_replies_json=:past_replies_json,
                            objections_json=:objections_json,
                            last_email_subject=:last_email_subject,
                            last_reply_classification=:last_reply_classification,
                            notes=:notes,
                            updated_at=:updated_at,
                            last_interaction_at=:last_interaction_at
                            WHERE lead_id=:lead_id"""),
                        record,
                    )
                else:
                    conn.execute(
                        text("""INSERT INTO lead_memory
                            (lead_id, past_emails_json, past_replies_json,
                             objections_json, last_email_subject,
                             last_reply_classification, notes, updated_at,
                             last_interaction_at)
                            VALUES (:lead_id, :past_emails_json, :past_replies_json,
                             :objections_json, :last_email_subject,
                             :last_reply_classification, :notes, :updated_at,
                             :last_interaction_at)"""),
                        record,
                    )
            log.debug("LeadMemory: updated lead %s", lead_id)
        except Exception as exc:
            log.warning("LeadMemory.update failed: %s", exc)

    def build_context_prompt(self, lead_id: int) -> str:
        """
        Return a formatted string snippet to inject into LLM prompts so the
        model is aware of past interactions.
        """
        mem = self.load(lead_id)
        if not any([mem["past_emails"], mem["objections"], mem["past_replies"]]):
            return ""

        lines = ["[Memory — previous interactions with this lead]"]
        if mem["last_email_subject"]:
            lines.append(f"Last email subject: {mem['last_email_subject']}")
        if mem["objections"]:
            lines.append("Known objections: " + "; ".join(mem["objections"]))
        if mem["last_reply_classification"]:
            lines.append(f"Last reply classification: {mem['last_reply_classification']}")
        if mem["notes"]:
            lines.append(f"Notes: {mem['notes']}")
        return "\n".join(lines)

    @staticmethod
    def _empty() -> dict:
        return {
            "lead_id": None,
            "past_emails": [],
            "past_replies": [],
            "objections": [],
            "last_email_subject": None,
            "last_reply_classification": None,
            "last_interaction_at": "",
            "notes": "",
        }


# Singleton
lead_memory = LeadMemory()
