# CLI and HTTP Share Use-Case Interfaces

## Status

Accepted and amended. ADR 0040 adds shared index sync, rebuild, and status use
cases, so the facade now exposes cascade lifecycle control on both entrypoints.
`doctor` and health still report queue and index status, and neither entrypoint
starts a background cascade.

The CLI exposes import, memory list, recall, eval, and doctor. The HTTP surface
exposes import, memory list, recall, evaluation run/report, and health.

Both entrypoints call the same use-case interfaces. HTTP adds validation,
request identifiers, and error envelopes, but no separate memory behavior.
