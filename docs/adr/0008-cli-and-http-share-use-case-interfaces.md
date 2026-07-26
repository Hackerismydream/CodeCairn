# CLI and HTTP Share Use-Case Interfaces

## Status

Accepted and implemented. `doctor`/health expose queue and index status, but
the shared facade does not currently expose cascade lifecycle controls for
sync, retry, or rebuild, and neither entrypoint starts a background cascade.

The CLI exposes import, memory list, recall, eval, and doctor. The HTTP surface
exposes import, memory list, recall, evaluation run/report, and health.

Both entrypoints call the same use-case interfaces. HTTP adds validation,
request identifiers, and error envelopes, but no separate memory behavior.
