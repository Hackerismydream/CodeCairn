# CLI and HTTP Share Use-Case Interfaces

## Status

Accepted and amended. ADR 0040 adds shared index sync, rebuild, and status use
cases. ADR 0048 makes CLI, MCP, and session-end hooks the version 0.1 product
surfaces; HTTP remains a compatibility adapter over shared use cases rather
than a parity requirement for every new lifecycle operation.

The CLI exposes import, memory list, recall, eval, and doctor. The HTTP surface
exposes import, memory list, recall, evaluation run/report, and health.

Both entrypoints call the same use-case interfaces. HTTP adds validation,
request identifiers, and error envelopes, but no separate memory behavior.
