"""
Pain point detection agent. Takes the research summary + signals and infers
the 2-4 most likely operational pains this lead's company has that map to
the sender's product — this output feeds directly into email/LinkedIn copy.
"""
import json
from gtm_engine.clients import llm_client
from gtm_engine.config import settings

SYSTEM_PROMPT = """[AGENT:PAIN_POINT] You are a pain point detection specialist for B2B sales.
Given company research and buying signals, infer the 2-4 most probable
operational pain points this company has that a document/contract
automation product could solve. Be specific and grounded in the research
provided — do not invent generic pains. Respond ONLY as JSON:
{"pain_points": ["...", "..."], "confidence": "low|medium|medium-high|high"}"""


def detect(research_output: dict, lead_id: int = None) -> dict:
    context = (
        f"Company summary: {research_output.get('summary')}\n"
        f"Signals: {', '.join(research_output.get('signals', []))}\n"
        f"Our product: {settings.sender_product_pitch}"
    )
    raw = llm_client.complete(SYSTEM_PROMPT, context, agent="pain_point", lead_id=lead_id)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"pain_points": [raw.strip()], "confidence": "low"}
    return parsed
