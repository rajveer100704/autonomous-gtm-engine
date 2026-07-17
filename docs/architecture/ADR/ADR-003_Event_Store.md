# ADR-003: Persistent Event Sourcing via Event Store

## Context
Standard relational audit logs capture static state transitions but fail to capture the complete causal chain of agent decisions. For debugging, tracing, and recovery, we need an append-only log of every domain event emitted.

## Decision
We implement a structured SQL-backed **Event Store** to persist every domain event with correlation and causation tracking metadata.

## Rationale
- **Causal Tracing:** Fields `correlation_id` and `causation_id` trace cascades of events across planners, workers, and executors.
- **Auditability:** Append-only log provides absolute historical certainty of outreach states.
- **Replayability:** Supports time-slice replays (`replay_between`), correlation replays, and filtering for audit analysis.

## Status
Accepted.
