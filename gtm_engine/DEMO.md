# Demo script — 5–7 minutes

A structure for walking someone through this project live, not a transcript to
read verbatim. Keep it moving; the console updates in real time, so let it do
the talking.

**Key renames vs. earlier drafts**: "Reflection" → **Quality Review**,
"Observability" → **Execution Trace**, "Pain Points" → **Detected Business
Challenges**. Use the new terms throughout.

---

## 0. Before you start (30 s — not part of the timed demo)

```bash
pip install -r requirements.txt
cp .env.example .env          # leave GTM_MOCK_MODE=true for the demo
uvicorn gtm_engine.api:app --reload
```

Open `http://localhost:8000/ui` in a browser tab, full screen.

---

## 1. The problem (30 s)

> "Most AI SDR tools stop at 'lead in, generic email out.' The gap is everything
> around that: is the email grounded in something real about the company? Does it
> improve over time? And can you tell *why* it did what it did? That's what this
> tests."

## 2. Architecture, fast (60 s)

Point at the architecture diagram in the README (or narrate from memory — do not
read the README live):

> "One FastAPI service, one database — Postgres in prod, SQLite for this demo.
> Lead sourcing, research, detected business challenge identification, email
> generation plus a quality review step, LinkedIn, and follow-up scheduling are
> all plain modules the orchestrator calls in order. No queue, no microservices —
> deliberately, until the workflow itself proves out."

## 3. Live campaign (2 min) — the core demo

- Click **Run campaign** on the Dashboard.
- While it runs, narrate:
  > "This is pulling leads from Apollo, grounding research through Tavily with a
  > per-domain cache, detecting business challenges, assigning an A/B variant,
  > generating the email, running a quality review agent before queuing it, then
  > generating LinkedIn copy."
- Once it lands: point at the **Pipeline** funnel and **Agent trace** panels.
- Click **Leads → a lead**. **This is the hero page — slow down here.**
  Walk through:
  1. Company info
  2. Research summary with evidence sources and a confidence score
  3. Detected Business Challenges panel (not just bullet points — grounded inferences)
  4. Generated email and LinkedIn copy (with variant label)
  5. Full Timeline — every agent step and funnel event in one chronological story

## 4. Quality Review, called out explicitly (45 s)

> "If you look at the timeline on this lead, you'll sometimes see a
> *Quality review passed* step — that's the quality review agent scoring the draft.
> If it scores too low, it forces a second pass with its own feedback folded in,
> and you'll see an *Email rewritten* step instead. One extra LLM call, but it
> measurably improves what actually gets sent instead of trusting the first
> generation."

## 5. Adaptive follow-ups + A/B (60 s)

- Go to **Analytics**. Point at the A/B panel:
  > "Two email styles, real reply-rate tracking, and an Evaluate A/B button that
  > promotes a winner once there's enough signal — 80/20 explore/exploit after
  > that, not a hard cutover."
- Mention reply classification:
  > "A reply doesn't just get a canned follow-up — it's classified (interested,
  > not now, out of office, unsubscribe...) and the remaining sequence adapts:
  > cancelled, pushed out, or left alone."

## 6. Execution Trace (45 s)

Go to **Execution Trace**:
> "Every agent call is timed and logged — no OpenTelemetry stack required to
> answer 'where does time go, and does it ever fail.' Type a lead id into the
> trace explorer to show a real per-lead span timeline."

Type a lead id live to show the bar chart of agent durations.

## 7. Closing (30 s)

> "The parts that are deliberately *not* here — a real Campaign entity,
> multi-source research grounding, a job queue, a planner/memory layer — are
> called out explicitly in the README's tradeoffs section. The goal wasn't to
> imitate Clay or Apollo's production infrastructure in a take-home; it was to
> prove the agent workflow, the judgment about what to build first, and honesty
> about what's next."

---

## If asked to go deeper

- **"Why Gemini?"** — swappable via `clients/llm_client.py`; not a hard dependency.
- **"Why not LangGraph/CrewAI?"** — the workflow is linear enough that a framework
  would add abstraction without adding capability yet; revisit if a planner or
  branching agent gets built.
- **"What breaks first at scale?"** — APScheduler (single process) and the lack of
  retries/dead-letter handling on LLM calls — both named in the README as the
  next production-hardening investment.
- **"Why a quality review step and not a better prompt?"** — failures become visible
  and auditable. A single larger prompt makes quality problems silent.

---

## Rehearsal checklist

Before the real demo, run through this once:

- [ ] API starts without errors (`uvicorn gtm_engine.api:app --reload`)
- [ ] Campaign runs in mock mode (4 leads, < 30 s)
- [ ] Lead detail page loads with research + challenges + outreach + timeline
- [ ] Execution Trace loads and trace explorer returns spans for a lead id
- [ ] Analytics page shows A/B bars (even if both are zero)
- [ ] You can narrate step 3 (the lead detail page) for 90 s without referring to notes
- [ ] You know one lead id by heart to use in the trace explorer live

**Total time target: 5–7 minutes.** Stop at 7; don't push to fill time.
