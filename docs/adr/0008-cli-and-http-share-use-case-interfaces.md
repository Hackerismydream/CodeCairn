# CLI and HTTP Share Use-Case Interfaces

The CLI exposes import, memory list, recall, eval, and doctor. The HTTP surface
exposes import, memory list, recall, evaluation run/report, and health.

Both entrypoints call the same use-case interfaces. HTTP adds validation,
request identifiers, and error envelopes, but no separate memory behavior.
