# Version 0.1 Retires HTTP Compatibility

## Status

Accepted.

## Context

The pre-v0.1 loopback HTTP adapter duplicated import, recall, index, and health
presentation already owned by the shared application service. It was not a
required Coding Agent integration: v0.1 clients use the CLI, stdio MCP, or
session-end hooks. Keeping the adapter added a server entrypoint, two direct
dependencies, a separate validation/error vocabulary, and another public
surface for learners to understand.

## Decision

Version 0.1 removes the loopback HTTP adapter and `codecairn-server` entrypoint.
The application service remains transport-independent. A future network API
requires a new product use case and ADR; it is not retained as speculative
compatibility.

This decision supersedes the HTTP-retention clauses of ADR 0008 and ADR 0048.
CLI, stdio MCP, and Codex/Claude hooks remain the supported product surfaces.

## Consequences

- the five-layer runtime has one application contract and three agent-facing
  presentations;
- FastAPI and Uvicorn are no longer direct product dependencies (the MCP SDK
  may retain server libraries transitively);
- existing users of the unpublished compatibility server must migrate to CLI
  or MCP;
- network authentication, remote tenancy, and HTTP parity remain outside v0.1.
