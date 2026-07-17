"""
Thin wrapper around Google's Gemini API. Every agent (research, pain-point
detection, email/LinkedIn copywriting) calls this one function so model
choice, retries and mock-mode live in exactly one place.

Real endpoint docs: https://ai.google.dev/api/generate-content
"""
import json
import requests
from gtm_engine.config import settings

MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
)


def complete(system: str, user: str, max_tokens: int = 800, agent: str = "unknown",
             lead_id: int = None) -> str:
    if settings.mock_mode or not settings.gemini_api_key:
        text = _mock_complete(system, user)
        _track_usage(system, user, text, agent, lead_id)
        return text

    headers = {"content-type": "application/json"}
    params = {"key": settings.gemini_api_key}
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.4},
    }
    resp = requests.post(GEMINI_URL, headers=headers, params=params, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates", [])
    text = ""
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)

    usage = data.get("usageMetadata", {})
    input_tokens = usage.get("promptTokenCount", _estimate_tokens(system + user))
    output_tokens = usage.get("candidatesTokenCount", _estimate_tokens(text))
    _record_usage(agent, input_tokens, output_tokens, lead_id)
    return text


def _estimate_tokens(text: str) -> int:
    """Rough estimate (~4 chars/token) used only when real usage isn't available."""
    return max(1, len(text) // 4)


def _track_usage(system: str, user: str, output: str, agent: str, lead_id):
    input_tokens = _estimate_tokens(system + user)
    output_tokens = _estimate_tokens(output)
    _record_usage(agent, input_tokens, output_tokens, lead_id)


def _record_usage(agent: str, input_tokens: int, output_tokens: int, lead_id):
    cost = (
        (input_tokens / 1000) * settings.gemini_input_cost_per_1k
        + (output_tokens / 1000) * settings.gemini_output_cost_per_1k
    )
    try:
        from gtm_engine.crm import db
        db.record_llm_usage(agent, input_tokens, output_tokens, round(cost, 8), lead_id)
    except Exception:
        # Cost tracking should never break the pipeline if the DB isn't ready yet.
        pass


def _mock_complete(system: str, user: str) -> str:
    """
    Deterministic canned responses so the pipeline is fully testable without
    a Gemini API key. Routes on an explicit [AGENT:X] tag each agent's
    SYSTEM_PROMPT is required to start with — no fuzzy keyword matching,
    so agent prompts can share vocabulary (e.g. "pain point", "follow-up")
    without colliding.
    """
    if "[AGENT:RESEARCH]" in system:
        return json.dumps({
            "summary": "Fast-growing legal-tech company digitizing contract workflows for mid-market law firms.",
            "signals": ["Raised a seed round in the last 2 quarters", "Hiring 3 open sales roles", "Recently posted about manual contract review pain on LinkedIn"]
        })
    if "[AGENT:PAIN_POINT]" in system:
        return json.dumps({
            "pain_points": ["Manual document review slows down deal velocity", "No centralized system for contract versioning", "Sales team spends hours per week on repetitive drafting"],
            "confidence": "medium-high"
        })
    if "[AGENT:FOLLOWUP]" in system:
        return json.dumps({
            "subject": "still worth a look, {first_name}?",
            "body": "Hi {first_name} — following up in case this got buried. One thing I didn't mention: teams at similar-sized companies usually see the biggest win in contract turnaround time specifically. No worries if now's not the moment."
        })
    if "[AGENT:REFLECT]" in system:
        pain_marker = "Pain point this email should address:"
        body_marker = "Body:"
        pain = ""
        if pain_marker in user and body_marker in user:
            pain = user.split(pain_marker, 1)[1].split("Subject:", 1)[0].strip().lower()
        body = user.split(body_marker, 1)[1].strip().lower() if body_marker in user else ""
        pain_keywords = [w.strip(".,") for w in pain.split() if len(w) > 5]
        hits = sum(1 for w in pain_keywords if w in body)
        if pain_keywords and hits / len(pain_keywords) < 0.15:
            return json.dumps({"score": 58, "feedback": "Body doesn't reference the specific pain point — reads generic."})
        return json.dumps({"score": 88, "feedback": "Specific and on-pain-point; CTA is low-friction."})
    if "[AGENT:EMAIL]" in system:
        if "style: curiosity" in user.lower():
            return json.dumps({
                "subject": "quick question, {first_name}",
                "body": "Hi {first_name},\n\nRandom question — how is {company} handling contract review as the team grows? Most teams your size tell us it quietly eats a few hours a week.\n\n{pitch}\n\nCurious if that's true for you too?\n\n{sender_name}"
            })
        return json.dumps({
            "subject": "quick question about contract turnaround at {company}",
            "body": "Hi {first_name},\n\nNoticed {company} has been scaling the sales team fast — usually that means contract and doc turnaround becomes the bottleneck.\n\n{pitch} teams like yours typically cut review time by ~40%.\n\nWorth a 15-min look?\n\n{sender_name}"
        })
    if "[AGENT:REPLY_CLASSIFY]" in system:
        text = user.lower()
        if "unsubscribe" in text or "remove me" in text or "stop emailing" in text:
            classification = "unsubscribe"
        elif "out of office" in text or "ooo" in text or "on leave" in text:
            classification = "out_of_office"
        elif "not interested" in text or "no thanks" in text or "please stop" in text:
            classification = "not_interested"
        elif "book a call" in text or "schedule a" in text or "calendly" in text or "let's meet" in text:
            classification = "meeting_request"
        elif "not right now" in text or "check back" in text or "next quarter" in text or "maybe later" in text:
            classification = "not_now"
        elif "interested" in text or "tell me more" in text or "sounds good" in text:
            classification = "interested"
        else:
            classification = "needs_more_info"
        return json.dumps({"classification": classification, "confidence": "medium"})
    if "[AGENT:LINKEDIN]" in system:
        return json.dumps({
            "message": "Hi {first_name} — saw {company} is growing the sales org. We help teams like yours automate contract/doc turnaround so reps aren't stuck waiting on redlines. Open to a quick chat?"
        })
    return "{}"
