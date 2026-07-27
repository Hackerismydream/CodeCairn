---
id: v01-001
scope: memory domain and obsolete write paths
status: ready
depends-on: [v01-000]
---

# Replace the six-type gated domain with four memory types

## Objective

Implement the durable record vocabulary in
[`../../v0.1/memory-lifecycle.md`](../../v0.1/memory-lifecycle.md), retain
system-derived Source evidence, and remove Evidence Gate as a write
prerequisite.

## Context

Current `memory/models.py`, `memory/evidence.py`, `memory/compression.py`, and
`service/runtime.py` encode six memory types, `MemoryProposal`, `GateDecision`,
and type-specific gate policy. Ordinary import bypasses most of that machinery.
ADRs 0043 and 0047 replace it.

## Paths

Primary:

- `src/codecairn/memory/models.py`
- `src/codecairn/memory/evidence.py`
- `src/codecairn/memory/compression.py`
- `src/codecairn/memory/episode.py`
- `src/codecairn/service/runtime.py`
- `src/codecairn/storage/markdown.py`
- `src/codecairn/storage/sqlite.py`
- `tests/test_episode_memory.py`
- `tests/test_evidence_gate.py`
- `tests/test_verified_fix_gate.py`

Add focused domain tests under `tests/` only if an existing file would mix
unrelated behavior.

## Required changes

1. Define the four closed memory types and common fields exactly as documented.
2. Define type-specific payload validation for Task Experience, Repository
   Knowledge, User Preference, and Work State.
   Implement the canonical identity, `subject_key`, and `workstream_key` rules
   from the lifecycle document; timestamps and model attempt IDs are excluded.
3. Preserve Evidence Reference and deterministic Evidence Fact derivation.
   Capture-derived Markdown stores the bounded selected fact snapshots; direct
   memory may have neither facts nor references.
4. Remove `MemoryProposal`, `GateDecision`, `EvidenceGate`, gate audit
   persistence, and gate-only tests.
5. Replace type-specific write entrypoints with one create-only
   `store_memory` service/storage contract.
6. Make `status` derived rather than an authored field; this task may return
   `active` while no Evolution Records exist.
7. Give manual/source-less memory `origin=agent_asserted`; do not synthesize
   evidence.
8. Detect the historical durable schema before any write and raise one typed
   “fresh root and re-import” error.
9. Delete obsolete modules or symbols once callers migrate; do not keep aliases
   for pre-release internal APIs.

## Public behavior

- valid four-type memory round-trips through Markdown and SQLite;
- an unknown historical type is rejected;
- an existing ID with identical content is idempotent;
- an existing ID with different content is a conflict;
- evidence references and fact snapshots round-trip but are not required for
  `agent_asserted` memory;
- source role, quote, command, file, and verification fields cannot be supplied
  through model-authored content.

## Verification

```bash
uv run pytest \
  tests/test_episode_memory.py \
  tests/test_markdown_store.py \
  tests/test_import_session.py
uv run pytest -q
uv run mypy
uv run lint-imports
make format
make check
rg "EvidenceGate|GateDecision|MemoryProposal|debug_episode|failed_command|verified_fix|repository_convention" src/codecairn
```

The final `rg` must return no live domain/write-path symbol. Historical
evaluation labels may remain only when required to verify an immutable artifact
and must be isolated outside the product memory model.

## Exit criteria

- four types are the only product Coding Memory variants;
- the public/service write path does not gate storage on verification;
- old roots fail before mutation;
- all checks pass;
- the commit reports product-core and total source-line deltas.
