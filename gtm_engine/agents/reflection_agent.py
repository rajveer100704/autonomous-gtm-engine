"""
Reflection agent. Generate -> Send was the old path. This inserts a judge
before send: score the draft, and if it's weak, ask for one rewrite with
the judge's feedback folded in. Cheap (one extra LLM call, only a second
one on a low score) and directly improves what actually goes out.
"""
import json
from gtm_engine.clients import llm_client

SYSTEM_PROMPT = """[AGENT:REFLECT] You are a ruthless cold-email quality reviewer for B2B SaaS.
Score the draft 0-100 on: specificity (not generic), brevity, a real
low-friction CTA, and whether it actually uses the pain point given rather
than ignoring it. Respond ONLY as JSON:
{"score": 0-100, "feedback": "one specific, actionable sentence"}"""

APPROVAL_THRESHOLD = 80


def judge(email_copy: dict, pain_point: str, lead_id: int = None) -> dict:
    context = (
        f"Pain point this email should address: {pain_point}\n"
        f"Subject: {email_copy.get('subject')}\n"
        f"Body: {email_copy.get('body')}"
    )
    raw = llm_client.complete(SYSTEM_PROMPT, context, agent="reflect", lead_id=lead_id)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"score": 70, "feedback": "Could not parse judge output."}
    parsed["approved"] = parsed.get("score", 0) >= APPROVAL_THRESHOLD
    return parsed
