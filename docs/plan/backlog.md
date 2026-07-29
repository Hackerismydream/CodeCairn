# Delivery Backlog

This file is an index, not a second source of requirements. Task files own
implementation detail.

| Order | Task | Exit signal |
|---:|---|---|
| 0 | [`v01-000`](tasks/v01-000-fable-baseline.md) | `main` contains Fable stack and baseline checks pass |
| 1 | [`v01-000a`](tasks/v01-000a-guardrails.md) | Source/CI gates and historical verifier boundary pass |
| 2 | [`v01-001`](tasks/v01-001-domain-slimming.md) | Four types compile; Evidence Gate write path is gone |
| 3 | [`v01-002`](tasks/v01-002-capture-pipeline.md) | Every Episode yields one experience; semantic work is retryable |
| 4 | [`v01-003`](tasks/v01-003-evolution-ledger.md) | Supersession, status rebuild, and restore pass |
| 5 | [`v01-004`](tasks/v01-004-active-recall.md) | Active-only typed recall and explicit history pass |
| 6 | [`v01-005`](tasks/v01-005-onboarding.md) | Clean-repo `init` to recall path passes |
| 7 | [`v01-006`](tasks/v01-006-mcp.md) | Seven MCP tools and one resource pass in process |
| 8 | [`v01-007`](tasks/v01-007-hooks.md) | Claude/Codex fixtures auto-import idempotently |
| 9 | [`v01-008`](tasks/v01-008-evaluation-slimming.md) | Eight Make targets pass or produce explicit paid-run commands; source budget passes |
| 10 | [`v01-009`](tasks/v01-009-packaging-learning.md) | Persistent install, license, package inventory, and docs pass |
| 11 | [`v01-010`](tasks/v01-010-release-e2e.md) | One clean implementation/evidence SHA pair owns all artifacts |

## Accepted post-v0.1 Pico integration

| Order | Task | Exit signal |
|---:|---|---|
| 12 | [`v02-001`](tasks/v02-001-pico-trace-import.md) | Pico journal replay and evidence-preserving import pass |
| 13 | [`v02-002`](tasks/v02-002-pico-memory-adapter.md) | Resolvable installed plugin exposes the CodeCairn backend |
| 14 | Pico `codecairn-001` and `codecairn-002` | Pico pins CodeCairn, removes EverOS, and retains Local Skills |
| 15 | [`v02-003`](tasks/v02-003-pico-integration-evidence.md) | Installed continuity, isolation, and paired artifacts are complete |

Post-v0.1 and still deferred:

- dynamic profiles;
- general Reflection, skills, or clustering;
- watcher daemon;
- dashboard and cloud service;
- formal same-harness EverOS comparison.
