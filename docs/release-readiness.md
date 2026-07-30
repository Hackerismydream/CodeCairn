# Release Readiness

Status: version 0.1 release candidate `f2358a7` passed its frozen release gates
and is published as tag `v0.1.0-rc1`. Version 0.2 Pico integration is
implemented; the first joint campaign completed with an ineligible positive
claim because unrelated queries received memory. ADR 0060 fixes that behavior.
The remaining v0.2 release step is an exact-pair installed rerun and release
binding.

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

## Version 0.1 release matrix

| Area | Current | Release requirement | Owner |
|---|---|---|---|
| Fable P0/B0 baseline | pass | retained | v01-000 |
| Contract hardening | documented | exact schema, Episode, recovery, evolution, freshness contracts retained | contract gate |
| Early guardrails | pass: 16,783 core / 34,285 total; benchmark-v3 pure reader verifies 4,411 files | retained and tightened at each stage | v01-000a |
| Four-type capture | implemented | retained through release smoke | v01-001/002 |
| Memory evolution | implemented | retained through release smoke | v01-003/004 |
| Onboarding | installed wheel initializes an empty Git repository | retained through final smoke | v01-005/009 |
| MCP | installed wheel exposes seven tools and one resource; real-client smoke passed | retained | v01-006/009/010 |
| Client hooks | real Claude SessionEnd and Codex Stop smoke, receipts, idempotency, and recall passed | retained | v01-007/010 |
| Evaluation commands | lifecycle, scale, retrieval, LoCoMo, coding A/B, and evidence verifier passed at the frozen candidate | retained; new protocols receive new identities | v01-008/010 |
| Source budget | 9,700 core / 13,978 total at v0.1 candidate | historical v0.1 gate retained; v0.2 additive budget applies | v01-001–010 / ADR 0058 |
| Package metadata | MIT, full metadata, curated 55-member wheel and 59-member sdist | retained | v01-009/010 |
| Installed smoke | isolated `uv tool` CLI/MCP/import/recall/hook dry-run/evidence pass | real Claude/Codex UI and hook receipt | v01-009/010 |
| Current evidence CI | v0.1 bundle builder/verifier and historical verifier pass | retained | v01-008/010 |
| Release benchmark | candidate `f2358a7`: LoCoMo 1,264/1,540, 82.08%; CodingMemoryBench +20 points with zero regressions | passed frozen v0.1 minimum | v01-010 |
| Governance | changelog, security, contribution, and conduct files present | retained | v01-009 |
| Tag/publication | `v0.1.0-rc1` | v0.2 exact-pair rerun before next release tag | release |

## Version 0.1 final gate

The frozen version 0.1 implementation/evidence pair passed:

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

It owns:

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

## Version 0.2 release delta

The next release must additionally pass the 120-case retrieval protocol with
unrelated-query injection at most 5%, then rerun installed Pico continuity,
isolation, and paired tasks against the exact CodeCairn dependency pin. The
earlier diagnostic remains immutable and does not become passing evidence
because the implementation changed.

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

The complete candidate audit and resume-safe wording are recorded in
[`v0.1/candidate-evaluation-646449b.md`](v0.1/candidate-evaluation-646449b.md).
