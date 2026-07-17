"""
agents/evaluation_agent.py — Two-stage content evaluation that gates execution.

Stage 1: Hard Rules (deterministic, instant)
  - Grammar / empty fields
  - Company name present
  - Correct recipient name
  - Email length within bounds
  - Spam trigger-word score

Stage 2: LLM Review (Gemini, ~200ms in mock)
  - Personalization depth
  - CTA clarity
  - Persuasiveness
  - Hallucination risk
  - Professionalism

Combined output:
    {
      "approved": True,
      "hard_checks": {"passed": True, "failures": []},
      "llm_review": {
        "overall_score": 8.4,
        "dimensions": { ... },
        "feedback": "..."
      },
      "combined_score": 8.4,
    }

Threshold configurable via EVAL_SCORE_THRESHOLD (default 7.0).
"""
from __future__ import annotations

import logging
import os
import re
import time

from gtm_engine.config import settings

log = logging.getLogger("gtm.evaluation_agent")

EVAL_THRESHOLD = float(os.getenv("EVAL_SCORE_THRESHOLD", "7.0"))

# ── Spam trigger words (weighted) ─────────────────────────────────────────
SPAM_TRIGGERS = {
    # word: penalty (higher = worse)
    "act now":        3.0,
    "limited time":   2.5,
    "buy now":        3.0,
    "click here":     2.0,
    "free":           1.5,
    "no obligation":  2.0,
    "guaranteed":     2.0,
    "100%":           1.5,
    "urgent":         2.0,
    "congratulations":2.5,
    "winner":         2.0,
    "dear friend":    3.0,
    "unsubscribe":    0.5,   # low — legitimate but flags
    "risk free":      2.5,
    "double your":    3.0,
    "cash bonus":     3.0,
    "mlm":            3.0,
}


# ═══════════════════════════════════════════════════════════════════════════
# Stage 1: Hard Rules (deterministic)
# ═══════════════════════════════════════════════════════════════════════════

def _hard_checks(
    email: dict,
    lead: dict,
    pain_points: list[str] | None = None,
) -> dict:
    """
    Returns {"passed": bool, "failures": [...], "spam_score": float}
    """
    subject = email.get("subject", "")
    body    = email.get("body", "")
    failures: list[str] = []

    # 1. Empty fields
    if not subject.strip():
        failures.append("empty_subject")
    if not body.strip():
        failures.append("empty_body")

    # 2. Unresolved placeholders
    for placeholder in ("{first_name}", "{company}", "{title}", "{domain}"):
        if placeholder in body or placeholder in subject:
            failures.append(f"unresolved_placeholder:{placeholder}")

    # 3. Wrong recipient name (mismatch)
    first_name = lead.get("first_name", "")
    if first_name and first_name not in body and first_name.lower() not in body.lower():
        failures.append("recipient_name_missing")

    # 4. Company name should appear somewhere
    company = lead.get("company", "")
    if company and len(company) > 2:
        if company.lower() not in body.lower() and company.lower() not in subject.lower():
            failures.append("company_not_mentioned")

    # 5. Length bounds (too short = lazy, too long = ignored)
    word_count = len(body.split())
    if word_count < 20:
        failures.append(f"body_too_short:{word_count}_words")
    if word_count > 400:
        failures.append(f"body_too_long:{word_count}_words")

    if len(subject) > 120:
        failures.append(f"subject_too_long:{len(subject)}_chars")

    # 6. Spam score
    body_lower = body.lower()
    spam_score = 0.0
    for trigger, penalty in SPAM_TRIGGERS.items():
        if trigger in body_lower:
            spam_score += penalty
    if spam_score > 6.0:
        failures.append(f"high_spam_score:{spam_score:.1f}")

    return {
        "passed":     len(failures) == 0,
        "failures":   failures,
        "spam_score": round(spam_score, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Stage 2: LLM Review
# ═══════════════════════════════════════════════════════════════════════════

_REVIEW_PROMPT = """You are a cold email quality reviewer. Score this email on each dimension (0-10).

LEAD CONTEXT:
Name: {first_name}
Company: {company}
Title: {title}
Pain points: {pain_points}

EMAIL:
Subject: {subject}
Body:
{body}

Score each dimension (0-10):
1. personalization — Does it reference the lead's specific situation?
2. cta_clarity — Is there exactly ONE clear ask?
3. persuasiveness — Would you reply to this?
4. hallucination_risk — Are there invented facts? (0=many fabrications, 10=all grounded)
5. professionalism — Tone, grammar, formatting

Return ONLY valid JSON:
{{"personalization": N, "cta_clarity": N, "persuasiveness": N, "hallucination_risk": N, "professionalism": N, "feedback": "one sentence"}}
"""


def _llm_review(email: dict, lead: dict, pain_points: list[str]) -> dict:
    """
    LLM-based quality review. Returns dimension scores + feedback.
    In mock mode returns deterministic scores based on content signals.
    """
    subject = email.get("subject", "")
    body    = email.get("body", "")

    if settings.mock_mode:
        return _mock_llm_review(body, lead, pain_points)

    # Real Gemini call
    from gtm_engine.clients.gemini_client import call_gemini, extract_json

    prompt = _REVIEW_PROMPT.format(
        first_name  = lead.get("first_name", ""),
        company     = lead.get("company", ""),
        title       = lead.get("title", ""),
        pain_points = "; ".join(pain_points or []),
        subject     = subject,
        body        = body,
    )

    raw = call_gemini(prompt, agent_name="evaluation")
    parsed = extract_json(raw)

    dims = {
        "personalization":    max(0, min(10, parsed.get("personalization", 5))),
        "cta_clarity":        max(0, min(10, parsed.get("cta_clarity", 5))),
        "persuasiveness":     max(0, min(10, parsed.get("persuasiveness", 5))),
        "hallucination_risk": max(0, min(10, parsed.get("hallucination_risk", 5))),
        "professionalism":    max(0, min(10, parsed.get("professionalism", 5))),
    }
    overall = sum(dims.values()) / len(dims)

    return {
        "overall_score": round(overall, 1),
        "dimensions": dims,
        "feedback": parsed.get("feedback", ""),
    }


def _mock_llm_review(body: str, lead: dict, pain_points: list[str]) -> dict:
    """Deterministic mock scores based on content heuristics."""
    body_lower = body.lower()
    first_name = lead.get("first_name", "").lower()
    company    = lead.get("company", "").lower()

    # Personalization: check if lead name + company + pain point are mentioned
    personalization = 5.0
    if first_name and first_name in body_lower:
        personalization += 2.0
    if company and company in body_lower:
        personalization += 1.5
    if any(pp.lower() in body_lower for pp in (pain_points or [])):
        personalization += 1.5

    # CTA: check for question mark or call-to-action words
    cta_clarity = 5.0
    cta_signals = ["would you", "can we", "interested in", "15 minutes", "quick call", "calendar"]
    cta_count = sum(1 for s in cta_signals if s in body_lower)
    if cta_count == 1:
        cta_clarity = 9.0
    elif cta_count >= 2:
        cta_clarity = 7.0  # multiple CTAs = slightly unfocused
    elif "?" in body:
        cta_clarity = 7.5

    # Persuasiveness: based on specificity
    persuasiveness = 6.0
    if any(pp.lower() in body_lower for pp in (pain_points or [])):
        persuasiveness += 2.0
    if len(body.split()) > 40:
        persuasiveness += 1.0

    # Hallucination: mock always passes (we trust our pipeline)
    hallucination_risk = 9.0

    # Professionalism: basic heuristics
    professionalism = 8.0
    if body_lower.startswith("hey "):
        professionalism -= 1.0
    if "!!!" in body:
        professionalism -= 1.5
    if body[0].isupper():
        professionalism += 0.5

    dims = {
        "personalization":    round(min(10, personalization), 1),
        "cta_clarity":        round(min(10, cta_clarity), 1),
        "persuasiveness":     round(min(10, persuasiveness), 1),
        "hallucination_risk": round(min(10, hallucination_risk), 1),
        "professionalism":    round(min(10, professionalism), 1),
    }
    overall = sum(dims.values()) / len(dims)

    return {
        "overall_score": round(overall, 1),
        "dimensions": dims,
        "feedback": "Mock review — content heuristics applied.",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Combined evaluation
# ═══════════════════════════════════════════════════════════════════════════

def evaluate(
    email: dict,
    lead: dict,
    pain_points: list[str] | None = None,
    threshold: float | None = None,
) -> dict:
    """
    Run two-stage evaluation:
      1. Hard rules (deterministic) — any failure = auto-reject
      2. LLM review (scored) — below threshold = reject

    Returns combined evaluation dict. Only emails with approved=True
    should proceed to the execution queue.
    """
    start = time.time()
    threshold = threshold or EVAL_THRESHOLD
    pain_pts = pain_points or []

    # Stage 1
    hard = _hard_checks(email, lead, pain_pts)

    # Stage 2 (only if hard checks pass — save LLM cost)
    if hard["passed"]:
        llm = _llm_review(email, lead, pain_pts)
    else:
        llm = {
            "overall_score": 0.0,
            "dimensions": {},
            "feedback": "Skipped — hard checks failed.",
        }

    combined_score = llm["overall_score"] if hard["passed"] else 0.0
    approved = hard["passed"] and combined_score >= threshold

    duration_ms = round((time.time() - start) * 1000, 1)

    result = {
        "approved":       approved,
        "combined_score": round(combined_score, 1),
        "threshold":      threshold,
        "hard_checks":    hard,
        "llm_review":     llm,
        "duration_ms":    duration_ms,
    }

    if approved:
        log.info("evaluation: APPROVED (score=%.1f threshold=%.1f)", combined_score, threshold)
    else:
        reasons = hard["failures"] if not hard["passed"] else [f"score {combined_score} < {threshold}"]
        log.warning("evaluation: REJECTED — %s", "; ".join(reasons))

    return result
