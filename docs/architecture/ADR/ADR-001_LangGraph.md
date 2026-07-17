# ADR-001: Stateful Agent Orchestration via LangGraph

## Context
The GTM Engine requires complex multi-agent execution flows that are non-linear, stateful, and require periodic human review or recovery checkpoints. Linear chains or static scripts fail when external network steps time out or when drafts need to be rejected and regenerated.

## Decision
We utilize **LangGraph** to model the campaign execution pipeline as a stateful directed graph.

## Rationale
- **State Persistence:** Built-in SQLite checkpointers allow saving state at every node, enabling campaigns to resume from failure points.
- **Human-in-the-Loop:** LangGraph provides native support for interrupting graph execution before high-risk nodes (e.g. queue nodes that commit outreach).
- **Cyclic Workflows:** Allows natural routing loops (e.g., fallback content regeneration loops when reflection reviews fail).

## Status
Accepted.
