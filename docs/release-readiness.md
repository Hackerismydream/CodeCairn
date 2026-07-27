# Release Readiness

Status: not release-ready. The product lifecycle through fixture-backed client
hooks is implemented, but evaluation, distribution, real-client smoke, and
release evidence gates remain.

## Baseline evidence

At the clean pre-development planning baseline `2c79b3f` on 2026-07-27:

```text
make format                                      pass
make check                                       pass: 661 tests, 82% rounded coverage
uv run codecairn evidence verify evidence/benchmark-v3
                                                  pass: 4,411 verified files
uv build                                          pass: wheel and sdist built
```

This proves the baseline checkout, not version 0.1 release readiness.

## Current matrix

| Area | Current | Release requirement | Owner |
|---|---|---|---|
| Fable P0/B0 baseline | pass | retained | v01-000 |
| Contract hardening | documented | exact schema, Episode, recovery, evolution, freshness contracts retained | contract gate |
| Early guardrails | pass: 16,783 core / 34,285 total; benchmark-v3 pure reader verifies 4,411 files | retained and tightened at each stage | v01-000a |
| Four-type capture | implemented | retained through release smoke | v01-001/002 |
| Memory evolution | implemented | retained through release smoke | v01-003/004 |
| Onboarding | implemented in checkout | installed-artifact smoke | v01-005/009 |
| MCP | seven tools and one resource pass in process | installed-package smoke | v01-006/009 |
| Client hooks | both fixtures import idempotently; installer is atomic | real Claude SessionEnd and Codex Stop smoke | v01-007/010 |
| Evaluation commands | historical complex CLI | eight documented Make targets | v01-008 |
| Source budget | 9,998 core / 12,594 total at v01-007 | at most 10,000 / 15,000 | v01-001–008 |
| Package metadata | partial | MIT, full metadata, curated artifacts | v01-009 |
| Installed smoke | absent | CLI/MCP/hook lifecycle outside checkout | v01-009/010 |
| Current evidence CI | stale bundle selection | selected release bundle verified | v01-008 |
| Release benchmark | historical 82.60% only | new full run at candidate commit, at least 82.00% | v01-010 |
| Governance | absent | changelog, security, contribution, conduct | v01-009 |
| Tag/publication | absent | verified implementation/evidence SHA pair and artifact inventory | v01-010 |

## Final gate

A clean implementation/evidence SHA pair must pass:

```text
make format
make check
make eval-smoke
make eval-scale
make eval-retrieval
make source-budget
make evidence-verify
uv build
fresh-environment installed-artifact smoke
```

It must also own:

- a full 1,540-question LoCoMo artifact at least 82.00%, target at least
  historical 82.60%;
- a local release-protocol recall P95 no greater than four seconds;
- 100-query Recall@5 at least 90%, provenance coverage 100%, and zero stale
  predecessor leakage;
- a complete coding memory-off/on A/B artifact with zero memory-induced
  regressions;
- 1,000-session/100,000-event duplicate-free scale import and 100% recovery at
  the eight Write Intent crash points;
- real supported-client hook receipts;
- curated wheel/sdist inventories and hashes;
- documentation/link/command verification.

The authoritative procedure is
[`v0.1/evaluation-and-release.md`](v0.1/evaluation-and-release.md); the
agent-executable final task is
[`plan/tasks/v01-010-release-e2e.md`](plan/tasks/v01-010-release-e2e.md).

## Evidence separation

Benchmark bundles and package releases have different lifecycles:

- an evidence bundle preserves an observed experiment at its own commit;
- a package release describes software at a tag;
- historical `benchmark-v3` remains verifiable after architecture changes;
- it cannot be relabeled as a version 0.1 result;
- build success does not prove artifact contents, installation, client
  integration, or benchmark completion.

No release language may replace a skipped, provider-blocked, fixture-only, or
historical gate with “pass.”
