---
status: accepted
---

# Pico Verified Outcomes Use Caller Identities

## Context

Pico's normal Memory Backend `store` operation has no stable caller identity,
so ADR 0057 correctly treats two independent calls as two batches. A Coding
Task run has a stronger contract: after deterministic verification it persists
one canonical outcome and retries delivery after crashes. Reusing ordinary
`store` would either duplicate Task Experiences or require unsafe content-wide
deduplication.

## Decision

Add one optional `store_verified_outcome(idempotency_key, outcome)` operation
to the installed Pico adapter. The key must be
`coding-task-outcome:<sha256(canonical-outcome)>`. It is both the journal
session identity and the input to a deterministic batch identity. The reserved
session prefix is rejected by ordinary `store`.

The adapter accepts only a canonical outcome marked `verified` with at least
one complete structured verification and no nonzero exit code. The bounded
Issue body has its own digest while a separate digest preserves the full input
identity. The adapter emits a user task,
matched `pico_done_gate` call and successful result, structured exit code and
file changes, and an assistant summary. Only the recognized terminal fields
author observed outcome and file-change evidence. The complete outcome is
retained as untrusted payload.

Under the journal lock, exact committed bytes are re-imported without append.
Conflicting staged or committed bytes and unexpected extra batches fail
closed. Ordinary `store` keeps its random batch identity and existing
semantics.

ADR 0058's `v02-002` limits remain historical. Add source stage `v02-004` with
ceilings of 11,125 non-evaluation core lines and 15,435 complete package lines.
This is a 125-line core allowance for the new delivery seam; tests and docs do
not consume the allowance.

## Consequences

- Pico can retry one verified result across process failure without duplicate
  memory;
- CodeCairn does not trust model prose or generalize deduplication;
- old Pico hosts continue to use the unchanged Memory Backend contract;
- installed acceptance must bind the Pico and CodeCairn commits together.
