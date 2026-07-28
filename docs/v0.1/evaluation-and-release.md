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
make eval-scale
make eval-retrieval
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

The smoke also fault-injects the eight Write Intent boundaries and proves that
hook capture followed by recall, without explicit `process`, returns the new
Task Experience or a typed `index_not_ready`, never stale success.

### `make eval-scale`

Offline and deterministic. A checked-in generator specification and seed
produce 1,000 synthetic, redacted sessions split equally across Codex and
Claude with 100,000 normalized events. The target imports every session twice
and emits one raw outcome per session plus the final Memory/Episode identity
inventory. The release verifier recomputes session, event, Episode, memory,
and duplicate counts from those records. Generated bulk inputs are excluded
from the installable package; their generator, manifest, digests, and raw
outcomes are checked in.

### `make eval-retrieval`

Runs the versioned 100-query repository-memory suite and emits per-query
candidates, selected memory, status, provenance, stale-predecessor observation,
latency, and aggregate Recall@5/precision values. It is the authoritative
source for retrieval and context-compilation resume claims.

### `make eval-locomo-200`

Runs the frozen 200-question diagnostic selection with an explicit answer,
judge, retrieval, budget, and repository identity. It is the normal iteration
loop and may spend provider money. The target refuses to run without an
explicit run ID and spend acknowledgement.

The diagnostic contains 50 questions from each category, while the release
dataset contains 282 multi-hop, 321 temporal, 96 open-domain, and 841
single-hop questions. Promotion therefore uses the frozen natural category
weights rather than the diagnostic's artificial 25% per-category mix. The
report publishes both raw diagnostic accuracy and
`natural_weighted_accuracy`; the latter is a promotion estimate, not a
1,540-question result. Promotion requires at least 82.00%, zero infrastructure
failures, and retrieval P95 at most 8.0 seconds under the two-worker pressure
protocol.

The diagnostic is a two-worker long-conversation pressure run, so its
retrieval P95 ceiling is 8.0 seconds. This is distinct from the 100-query
product retrieval suite, whose P95 release gate remains 4.0 seconds.

The answer and judge instruction contracts are versioned protocol inputs.
OpenAI-compatible requests deliver each instruction both as the system message
and as a prefix to the user message. This is intentional transport
compatibility, not an additional hidden prompt: some intermediaries replace
the supplied system message. The run manifest records the exact endpoint,
model alias, instruction contract, and prompt-delivery revision. Right Code's
current Codex documentation uses `https://rightapi.ai/codex/v1`; each paid run
must still discover the model and complete a bounded preflight before scoring.
See the official
[Codex configuration](https://docs.right.codes/docs/rc_cli_config/codex) and
[request compatibility](https://docs.right.codes/docs/rc_extension/curl)
documentation.

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

### Version 0.1 release bundle

The release candidate uses the repository-only
`scripts/build_release_bundle.py` reducer. It copies only public outcome fields:
LoCoMo provider attempts/runtime state, Coding workspaces/raw agent logs,
credentials, and owned transcripts are excluded. The installed
`codecairn evidence verify` command detects the
`codecairn-v01-release-evidence-v1` contract while retaining the historical
bundle reader. Coding traces retain only step, event kind, exit status, and
command/path hashes; the verifier recomputes the three trace metrics from those
redacted events. It also recomputes smoke, scale, and retrieval threshold
metrics from their public raw outcomes rather than trusting stored aggregates.
Installed and real-client smoke wheel hashes must match both clean
reproducible builds.

After freezing `implementation_sha`, generate each offline run with an explicit
`RUN_ID`, save the recovery JUnit report, package reports, and real-client
report, then build:

```bash
uv run python scripts/build_release_bundle.py \
  --bundle-id v0.1-rc1 \
  --implementation-sha "$IMPLEMENTATION_SHA" \
  --smoke "$SMOKE_RUN" \
  --scale "$SCALE_RUN" \
  --retrieval "$RETRIEVAL_RUN" \
  --locomo-200 "$LOCOMO_200_RUN" \
  --locomo-full "$LOCOMO_FULL_RUN" \
  --coding "$CODING_RUN" \
  --recovery-junit "$RECOVERY_JUNIT" \
  --real-clients "$REAL_CLIENT_REPORT" \
  --installed-smoke "$INSTALLED_SMOKE_REPORT" \
  --artifact-repro "$ARTIFACT_REPRO_REPORT" \
  --source-budget "$SOURCE_BUDGET_REPORT" \
  --quality "$QUALITY_REPORT" \
  --release-notes "$RELEASE_NOTES"
EVIDENCE_BUNDLE=evidence/v0.1-rc1 make evidence-verify
```

`scripts/real_client_smoke.py` installs the candidate wheel into an isolated
tool directory, exercises real Codex and Claude processes, verifies native hook
receipts, repeats the exact boundaries, recalls the captured memories, and
restores the isolated config bytes. Claude accepts an isolated provider route
through the names of three environment variables; without those options it uses
the existing authenticated identity and an explicit project/local
setting-source allowlist. Both paths delete only the UUID-owned smoke transcript
after verification:

```bash
uv run python scripts/real_client_smoke.py \
  --wheel dist/codecairn-0.1.0-py3-none-any.whl \
  --implementation-sha "$IMPLEMENTATION_SHA" \
  --output benchmark_results/release/real-clients.json \
  --spend-ack YES \
  --claude-max-budget-usd "$APPROVED_CLIENT_SMOKE_CEILING" \
  --claude-api-key-env ANTHROPIC_API_KEY \
  --claude-base-url-env ANTHROPIC_BASE_URL \
  --claude-model-env ANTHROPIC_MODEL
```

The script has no default paid allowance: both the acknowledgement and a
positive finite Claude ceiling are required before either client starts.

The builder may verify an uncommitted bundle while `HEAD` still equals
`implementation_sha`. Release acceptance requires a clean evidence-only direct
descendant; the final verifier then reports `evidence_binding.status=bound` and
`direct_descendant=true`.

## Artifact contract

Every scored run records:

| Field | Requirement |
|---|---|
| `run_id` | immutable and unique |
| `implementation_sha` | clean full SHA containing all executed code |
| `evidence_sha` | absent from run identity; supplied by the verifier as the clean commit containing the final bundle |
| `protocol_version` | checked-in immutable file |
| `dataset_identity` | digest and selection |
| `provider_identity` | role, endpoint class, model, revision status |
| `retrieval_profile` | provider, model, dimension, adapter, reranker |
| `budget` | context, calls, concurrency, timeout, spend ceiling |
| `raw outcomes` | one explicit outcome per selected item |
| `aggregate` | pure reducer output |
| `environment` | Python/platform and relevant non-secret versions |

Reports are pure readers. They never retry, call providers, or mutate a run.
`repository_commit` in historical schemas maps to `implementation_sha`; new
version 0.1 schemas never overload one SHA. `evidence_sha` is not embedded in
its own commit, which would be a self-reference. The verifier receives or
derives the clean evidence HEAD and proves that HEAD contains the checked
inventory and directly descends from `implementation_sha`.

## Current evidence boundary

`evidence/benchmark-v3` remains the current checked-in historical bundle. It
reports 82.60% on 1,540 LoCoMo category 1–4 questions under its frozen
architecture and protocol. The bundle remains valid if its verifier passes,
but it cannot be attributed to the version 0.1 four-type/evolution design.

A version 0.1 release candidate must generate a new run and bundle at the
candidate commit. Until that run exists, release score rows are “not run,” not
82.60%.

## Release thresholds

All execution gates bind to one clean `implementation_sha`; the final bundle
binds the allowed `evidence_sha` described below:

| Gate | Threshold |
|---|---|
| Format, lint, types, imports, tests | pass |
| Installed CLI/MCP/hook smoke | pass |
| Lifecycle smoke | pass |
| Scale import | 1,000 sessions / 100,000 events; repeated import creates zero duplicate Episode or Memory |
| Write Intent recovery | 8 crash points across capture, direct memory, evolution, and restore; 100% deterministic recovery |
| Hook read-your-writes | 100% for both fixture families without explicit `process` |
| Retrieval | 100-query Recall@5 at least 90.00%; provenance coverage 100%; stale predecessor leakage 0% |
| LoCoMo full | at least 82.00%; ship target 85.00% to 86.00% |
| Product recall latency | 100-query suite P95 at most 4.0 seconds |
| Coding A/B | complete 20-task artifact; memory-induced regression count 0; observed delta published |
| Evidence verifier | pass on new bundle |
| Product core source | at most 10,000 physical Python lines; internal target 9,700 |
| Total package source | at most 15,000 physical Python lines; internal target 14,100 |
| Wheel and sdist | curated, reproducible inventory; clean install pass |
| Documentation | links, commands, terms, and walkthrough pass |
| License/governance | MIT, security, contribution, conduct, changelog |

The LoCoMo minimum prevents a known regression. Version 0.1 stops retrieval
optimization once the full result is inside the 85% to 86% ship band; a higher
score is not a release requirement. Coding A/B publishes the observed delta
rather than selecting a result after seeing it; the release safety gate is
zero tasks that pass memory-off and fail because of supplied active memory. A
resume claim of improvement requires an observed increase of at least one of
20 tasks (5 percentage points); otherwise the product result is published
without that claim.

## Source-budget command

The checked-in script counts newline-delimited physical `.py` lines under
`src/codecairn`:

```text
core  = all .py files excluding src/codecairn/evaluation/
total = all .py files under src/codecairn/
```

The evaluation worker lives under `src/codecairn/evaluation/` and is counted as
evaluation. `src/codecairn/locomo_worker.py` is not a permitted second
classification. CI and reports use this one definition.

The `v01-000a` transition ceiling is 17,250 core / 34,300 total. It permits
only the pure historical-reader boundary added by that task while moving the
existing worker into the single evaluation classification. The immutable
baseline remains 17,250 core / 16,841 evaluation / 34,091 total; current
counts and baseline counts are always reported separately. `v01-001` tightens
the core ceiling to 15,500 and does not inherit extra total headroom.

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

The source counter and historical verifier characterization land before domain
replacement. Stage gates are:

- before `v01-001`: deterministic counter and CI failure exist;
- after `v01-001`: core at most 15,500;
- after `v01-004`: core at most 11,500;
- after `v01-007`: core at most 10,000;
- after `v01-008`: the 10,000/15,000 public ceilings remain, while the
  source-budget command enforces the stricter 9,700/14,100 internal release
  target.

Any new old-API alias, dual write, or parallel retrieval mode matrix blocks the
owning task.

## Coding-native product evidence

The release bundle publishes, in addition to LoCoMo:

- hook-to-recall read-your-writes success;
- stale predecessor leakage rate;
- selected-memory source-attribution coverage;
- cross-session continuation task outcomes;
- memory-off/on verifier outcomes and regression count;
- selected-memory precision and recall latency;
- every failure caused by an incorrect active revision.

## Release sequence

1. Freeze the protocol and clean `implementation_sha`.
2. Run all offline checks, scale/retrieval suites, and installed-artifact
   smoke.
3. Discover the configured answer/judge models and complete bounded real
   request preflights.
4. Run LoCoMo-200 to completion as a cost and correctness rehearsal; promote
   only when its frozen natural-weighted gate reaches 82.00%, with zero
   infrastructure failures and retrieval P95 at most 8.0 seconds under the
   two-worker pressure protocol.
5. Run the full LoCoMo protocol once; repair only exact infrastructure
   failures through a separate immutable run.
6. Run coding A/B only after the full LoCoMo gate passes.
7. Build a new evidence bundle and commit only generated evidence/docs as the
   direct `evidence_sha` descendant.
8. Check redaction and run `make evidence-verify`; the verifier binds the
   checked inventory to the clean current HEAD as `evidence_sha`.
9. Re-run `make check`, `make source-budget`, `make eval-smoke`, and
   `make evidence-verify` at `evidence_sha`.
10. Build wheel/sdist twice from clean checkouts and compare inventories.
11. Tag `evidence_sha`. Any code change starts again with a new
    `implementation_sha`.

## Deferred evaluation

- formal same-harness EverOS comparison;
- Raven integration benchmark;
- adversarial category-5 release gate;
- production traffic or multi-machine latency claims.
