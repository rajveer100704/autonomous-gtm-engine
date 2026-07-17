# ADR-005: Discoverable Agent Tools via MCP Tool Registry

## Context
When agents require access to external capabilities (Gmail, LinkedIn, Search engines), calling python execution functions directly couples prompt generation with execution code. We need a standardized tool catalog offering schema validation and execution policies.

## Decision
We implement a unified **MCP Tool Registry** that maps unique tool identifiers to executable Python functions.

## Rationale
- **Schema Validation:** Holds standard JSON schemas for inputs and outputs, allowing planners to auto-validate arguments.
- **Execution Policies:** Every tool records metadata such as expected latency, timeouts, permissions, and cost to allow cost/safety audits.
- **MCP Alignment:** Positions the system to naturally integrate with future Model Context Protocol (MCP) clients.

## Status
Accepted.
