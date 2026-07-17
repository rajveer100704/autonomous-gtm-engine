# ADR-002: Distributed Queue Backend via Redis Streams

## Context
As the execution queue moves from single-worker processes to horizontally scaled worker fleets, the database-backed SQLite queue suffers from write-locking under concurrency. We need a performant message broker that supports task ownership, consumer groups, and atomic claiming.

## Decision
We abstract the execution queue behind a pluggable `BaseQueueAdapter` and implement a production `RedisQueueAdapter` using **Redis Streams**.

## Rationale
- **Consumer Groups:** Native support for consumer groups allows parallel workers to pull tasks without duplicates, tracking task state in flight.
- **Durable PEL:** Pending Entries List (PEL) tracks task owners, enabling crash recovery if a worker dies before calling ACK.
- **Pluggable Abstraction:** Wrapping it in `BaseQueueAdapter` allows local testing using SQLite with zero setup.

## Status
Accepted.
