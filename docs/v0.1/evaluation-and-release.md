# Version 0.1 Evaluation and Release

## Principle

CodeCairn publishes an observed result only when the repository contains the
run manifest, raw aggregate inputs, reducer output, and offline-verifiable
inventory. A passing fixture, provider endpoint, historical bundle, or planned
command is not a release result.

## One-command surfaces

Version 0.1 adds these authoritative Make targets:

```bash
make eval-smoke
make eval-locomo-200
make eval-locomo-full
make eval-coding-ab
make evidence-verify
make source-budget
```

### `make eval-smoke`

Offline and deterministic. It creates a temporary isolated root and proves:

1. Codex and Claude fixtures normalize;
2. one Episode produces one Task Experience;
3. all four memory types can be created;
4. open-to-open and open-to-closed Work State plus Preference supersession
   apply;
5. active recall excludes predecessors;
6. history and restore preserve a forward-only chain;
7. MCP tools/resource return the same identities;
8. both hook fixtures import idempotently;
9. index rebuild reaches parity.

It is a release gate but not a benchmark score.

### `make eval-locomo-200`

Runs the frozen 200-question diagnostic selection with an explicit answer,
judge, retrieval, budget, and repository identity. It is the normal iteration
loop and may spend provider money. The target refuses to run without an
explicit run ID and spend acknowledgement.

### `make eval-locomo-full`

Runs all 1,540 category 1–4 questions under a versioned release protocol. It:

- requires a clean commit;
- records exact provider roles and mutable-alias limitations;
- records every selected question outcome, including exhausted failures;
- never repairs the base run in place;
- emits the exact evidence-bundle input path.

This is the only LoCoMo result that can satisfy the release score gate.

### `make eval-coding-ab`

Runs the same checked-in coding tasks twice in physically isolated workspaces:

- memory off;
- memory on through the public recall interface.

Task inputs, agent configuration, verifier commands, timeout, and outcome are
recorded. A missing agent or credential is an infrastructure failure, not a
failed or successful task.

### `make evidence-verify`

Offline. It recomputes aggregates, generated copy, exact-repair membership,
source consistency, and the SHA-256 inventory for the selected checked-in
bundle. It proves artifact integrity, not provider replay or semantic truth.

## Artifact contract

Every scored run records:

| Field | Requirement |
|---|---|
| `run_id` | immutable and unique |
| `repository_commit` | clean full SHA |
| `protocol_version` | checked-in immutable file |
| `dataset_identity` | digest and selection |
| `provider_identity` | role, endpoint class, model, revision status |
| `retrieval_profile` | provider, model, dimension, adapter, reranker |
| `budget` | context, calls, concurrency, timeout, spend ceiling |
| `raw outcomes` | one explicit outcome per selected item |
| `aggregate` | pure reducer output |
| `environment` | Python/platform and relevant non-secret versions |

Reports are pure readers. They never retry, call providers, or mutate a run.

## Current evidence boundary

`evidence/benchmark-v3` remains the current checked-in historical bundle. It
reports 82.60% on 1,540 LoCoMo category 1–4 questions under its frozen
architecture and protocol. The bundle remains valid if its verifier passes,
but it cannot be attributed to the version 0.1 four-type/evolution design.

A version 0.1 release candidate must generate a new run and bundle at the
candidate commit. Until that run exists, release score rows are “not run,” not
82.60%.

## Release thresholds

All gates bind to one clean candidate commit:

| Gate | Threshold |
|---|---|
| Format, lint, types, imports, tests | pass |
| Installed CLI/MCP/hook smoke | pass |
| Lifecycle smoke | pass |
| LoCoMo full | at least 82.00%; target at least 82.60% |
| Local recall latency | P95 at most 4.0 seconds under release protocol |
| Coding A/B | complete artifact; no fabricated improvement threshold |
| Evidence verifier | pass on new bundle |
| Product core source | at most 10,000 physical Python lines |
| Total package source | at most 15,000 physical Python lines |
| Wheel and sdist | curated, reproducible inventory; clean install pass |
| Documentation | links, commands, terms, and walkthrough pass |
| License/governance | MIT, security, contribution, conduct, changelog |

The LoCoMo minimum prevents a known regression. The target preserves the
historical headline. Coding A/B publishes the observed delta rather than
selecting a result threshold after seeing it.

## Source-budget command

The checked-in script counts newline-delimited physical `.py` lines under
`src/codecairn`:

```text
core  = all .py files excluding src/codecairn/evaluation/
total = all .py files under src/codecairn/
```

It prints the commit, included paths, per-area totals, ceilings, and pass/fail.
Tests, docs, generated artifacts, and caches are excluded. Installable code
moved to a different package is included by policy. CI runs this target.

The baseline recorded in ADR 0049 is:

```text
main@954f728
core:  17,250
eval:  16,841
total: 34,091
```

The reduction work must remove obsolete behavior, not minify readable code.

## Evaluation simplification

The current evaluation package contains historical protocol evolution and
one-off orchestration. Version 0.1 keeps:

- immutable protocol assets needed by the current run;
- a generic run journal and provider boundary;
- LoCoMo diagnostic/full runner;
- coding A/B runner;
- evidence reducer and verifier;
- historical bundle verification.

It removes or archives outside installable source:

- superseded protocol-specific orchestration;
- duplicate retry/promotion paths that can become one generic exact-repair
  operation;
- benchmark-only copies of runtime behavior;
- unused oracle and experiment helpers not required to reproduce a published
  artifact.

Frozen bundles remain immutable. If old verification requires a format reader,
that small reader remains; old execution frameworks do not.

## Release sequence

1. Freeze the protocol and candidate commit.
2. Run all offline checks and installed-artifact smoke.
3. Run LoCoMo-200 as a cost and correctness rehearsal.
4. Run the full LoCoMo protocol once; repair only exact infrastructure
   failures through a separate immutable run.
5. Run coding A/B.
6. Build and check the public evidence bundle.
7. Re-run `make check`, `make source-budget`, and `make evidence-verify`.
8. Build wheel/sdist twice from clean checkouts and compare inventories.
9. Tag only the exact verified commit.

## Deferred evaluation

- formal same-harness EverOS comparison;
- Raven integration benchmark;
- adversarial category-5 release gate;
- production traffic or multi-machine latency claims.
