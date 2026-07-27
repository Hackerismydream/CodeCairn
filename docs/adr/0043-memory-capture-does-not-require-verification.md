---
status: accepted
amended-by: 0051
---

# Memory Capture Does Not Require Verification

CodeCairn stores useful model-authored interpretations without requiring them
to pass a type-specific Evidence Gate. The system still authors namespace,
source references, roles, command outcomes, file changes, quotes, and
verification status from normalized events; model output may author memory
content and relationships but not provenance.

Verification becomes an optional audit operation. Removing it from the write
path makes direct Agent memory and session-end capture practical while
preserving a clear distinction between source observation, interpretation, and
verified interpretation.
