# ADR-004: Decoupled Planning via Goal Manager

## Context
In earlier campaign orchestration flows, the Strategy Agent directly decided which outreach to generate and enqueue. This mixed tactical content generation with high-level budget constraints, success criteria tracking, and deadlines.

## Decision
We introduce a dedicated **Goal Manager** node in the LangGraph topology, separating campaign-level goal validation from copy generation.

## Rationale
- **Declarative Goals:** Allows representing campaign aims as structured schemas (Success Criteria, Constraints, Budget, Deadline).
- **Cost Controls:** Integrates with `BudgetManager` to dynamically route critical decisions to expensive LLMs and repetitive research tasks to cheaper LLMs.
- **Separation of Concerns:** The Strategy Agent is simplified to handle only *how* to draft outreach under goal guidelines.

## Status
Accepted.
