# Public Evidence Bundle

CodeCairn publishes benchmark claims through generated, immutable evidence
bundles. A current bundle is intended to be an offline-verifiable reduction of
already completed run artifacts. It is not a promise that the experiment can
be repeated later with bit-for-bit vectors, identical model output, or the same
score. Retained historical bundles can also expose verifier-compatibility drift;
that state is reported below rather than hidden by editing immutable artifacts.

The current published bundle is
[`evidence/benchmark-v3`](../evidence/benchmark-v3/README.md).

## Bundle lineage

Earlier bundles remain immutable historical artifacts.

| Bundle | LoCoMo status | Quality snapshot | Role |
|---|---|---|---|
| [`benchmark-v1`](../evidence/benchmark-v1/README.md) | 10-question unscored smoke; no accuracy claim | 148 tests; generated coverage label 83.50% | First public bundle and current CI smoke target |
| [`benchmark-v2`](../evidence/benchmark-v2/README.md) | First 1,540-question score: 47.73%; label-only category amendment | 171 tests; generated coverage label 83.53% | Historical full-run evidence; current verifier compatibility is broken |
| [`benchmark-v3`](../evidence/benchmark-v3/README.md) | V23 exact-repair composite: 82.60% over 1,540 category 1-4 questions | 644 tests; combined line/branch coverage 81.53% | Current public evidence |

Bundle IDs describe artifact generations, not package releases.

## Build contract

`codecairn evidence build` consumes completed immutable run directories plus
quality artifacts:

```bash
uv run codecairn evidence build \
  --bundle-id <new-bundle-id> \
  --locomo-run /path/to/locomo-run-or-composite.json \
  --retrieval-run /path/to/retrieval-run \
  --recovery-run /path/to/recovery-run \
  --coding-run /path/to/coding-run \
  --quality-junit /path/to/junit.xml \
  --quality-coverage /path/to/coverage.json \
  --generator-commit <full-commit> \
  --repository-root . \
  --output-root evidence
```

The output path is exclusive; an existing bundle is never overwritten.

The reducer treats saved suite summaries as assertions. It recomputes LoCoMo,
retrieval, recovery, and CodingMemoryBench aggregates from their public raw
records and rejects value-level differences. It then generates metrics,
inventory counts, README text, and recruiting copy.

### What the builder machine-checks

- input artifact schemas required by each suite;
- immutable output path;
- report values against the raw records copied into the public bundle;
- public redaction and required source receipts;
- a non-empty caller-supplied generator commit;
- the dependency-lock hash and environment of the reducer checkout;
- final SHA-256 inventory.

### What the builder does not machine-check

- that `generator_commit` equals the repository's clean `HEAD`;
- that JUnit and coverage files were produced by that commit;
- that older source runs used the bundle-level dependency lock or environment;
- that a model alias resolves to the same provider-side weights later;
- that a coding agent without seed support will reproduce the same trajectory.

These are provenance limitations, not reasons to discard the observed result.
They prevent stronger claims of same-checkout or future score-identical
reproduction.

## Public content and redaction

The reducer copies only the records needed to recompute public claims.

Retrieval records retain query identity, ranking outcome, metrics, and source
artifact hashes. Recovery retains deterministic checks. Coding records retain
normalized traces and hidden-verifier outcomes while excluding final
workspaces, local paths, and stderr.

Ordinary LoCoMo bundles retain normalized question checkpoints but exclude the
licensed dataset question, gold answer, evidence text, recalled memory, and raw
judge responses. Public ingest records retain identifiers and aggregate counts
without speaker names or runtime paths.

An exact-repair composite retains:

1. source manifest and report receipts;
2. the frozen target and repair selections;
3. privacy-safe base and repair outcomes;
4. one final outcome for every target question with `source=base|repair`;
5. aggregate ingest records required to prove dataset scale.

Runtime databases, vector indexes, provider secrets, source workspaces, private
traces, and the LoCoMo dataset are never copied.

## Exact-repair interpretation

`benchmark-v3` combines an immutable base run with an exact repair of all and
only its 717 infrastructure failures. The base negative artifact remains
visible.

The final composite has formal scored outcomes for all 1,540 selected
questions. Of those, 1,538 reached the configured three-vote judge. Two repair
questions exhausted the frozen answer contract and were scored wrong without
judge votes.

Therefore:

- `100%` means complete formal scored-outcome coverage;
- it does not mean every question completed the answer-and-judge happy path;
- generated copy saying all 1,540 questions received three judge votes is
  over-broad historical wording and must be corrected by a future generator,
  not by hand-editing v3.

## Quality metric interpretation

The v3 raw Coverage.py totals are:

| Metric | Value |
|---|---:|
| Statement coverage | 86.17% |
| Branch coverage | 69.11% |
| Combined statement/branch coverage | 81.53% |

The v3 generator labels `81.53%` as `Statement coverage`, but its reducer reads
Coverage.py's combined `totals.percent_covered`. The number is reproducible; the
generated label is inaccurate. A future bundle generator must correct it while
v3 remains immutable.

CI has no coverage fail-under. These are bundle snapshots, not a continuously
enforced minimum.

## Suite boundaries in v3

| Suite | What the artifact demonstrates | Important limitation |
|---|---|---|
| Retrieval | Historical 20-memory, 100-query relevance result | `retrieval-fbc7023` used hashing/fusion; its manifest lacks model, lock, and environment identity |
| Recovery | Six deterministic checks and 100% parent/document parity on a two-memory synthetic fixture | Not a production-scale daemon or provider-failure test |
| CodingMemoryBench | Checked-in pre-retrieved context changed hidden-verifier outcomes over 120 isolated runs | It does not run CodeCairn import, indexing, or retrieval |
| LoCoMo | 272 attributed Conversation Episodes, DashScope `text-embedding-v4`/local CrossEncoder recall, answer, and judge path | It bypasses the Codex/Claude JSONL importer and public application facade |

The v3 retrieval values—96.00% Recall@5, 0.7979 MRR, and 10.91 ms P95—must not
be attributed to the current DashScope plus local CrossEncoder production
profile. A new standalone retrieval artifact is required for that claim.

No current bundle proves the public
`Codex/Claude import -> index lifecycle -> recall` product loop.

## Cost interpretation

The v3 bundle records `6.31437348 CNY` for LoCoMo source reports. That value
covers the observed answer/judge usage represented by the composite sources.
It is not a complete total for corpus construction, semantic projection,
embedding, or every external service involved in preparing the run.

CodingMemoryBench provider cost remains pending because the captured coding
trace exposes no cost observation.

## Offline verification

Verify the current bundle with:

```bash
uv run codecairn evidence verify evidence/benchmark-v3
```

Verification requires no provider credential, private trace, hidden workspace,
or LoCoMo dataset. It checks:

- the complete file inventory and SHA-256 hashes;
- suite aggregates from published query/outcome/check records;
- exact-repair source and ID-set consistency;
- scale counts and generated metrics;
- generated README and recruiting copy.

It does not:

- rerun an embedding, answer, judge, or coding agent;
- re-evaluate semantic correctness;
- execute hidden-verifier commands;
- prove quality-artifact checkout identity;
- prove current product-entrypoint behavior.

The correct description is **offline artifact-integrity verification**, not a
full experiment rerun.

## CI boundary

Current CI runs `make check`, verifies `evidence/benchmark-v1`, and builds
package artifacts. It does not verify `benchmark-v3`.

Until the workflow changes, v3 may be described as locally offline-verified,
but not as protected by the main-branch CI gate.

## Historical label-only amendment

`benchmark-v2` contains a known historical LoCoMo category-label mapping
amendment. The reducer may apply only the declared legacy-to-current label
replacement when every numeric value and all other report content match. The
original summary hash and exact label changes remain in the bundle. Arbitrary
report drift is rejected.

On current main, `codecairn evidence verify evidence/benchmark-v2` fails with
`Saved LoCoMo report does not match recomputed data`. The current reducer adds
`model_output_scoring_contract=contract-exhausted-answer-is-wrong-v1`, while the
immutable v2 saved summary predates that field. This is a verifier
backward-compatibility gap, not evidence that v2 now passes or that its artifact
should be hand-edited. `benchmark-v1` and the current `benchmark-v3` verify
under the same checkout.

## Interpretation rules

- LoCoMo category 1 is multi-hop, 2 temporal, 3 open-domain, 4 single-hop, and
  5 adversarial.
- Published v3 accuracy covers the frozen answerable category 1-4 selection.
  Category 5 is excluded and must not be silently treated as answerable.
- A LoCoMo smoke run validates plumbing only and is never an accuracy result.
- CodingMemoryBench compares the same 20 controlled tasks, three repeats, and
  isolated workspaces with a verifier hidden from the agent.
- Retrieval latency is a historical single-machine observation, not a service
  SLO or current production-provider latency.
- Controlled public fixtures are not private production traces.
- Provider configuration or endpoint reachability is not a successful run.

LoCoMo is sourced from the official SNAP Research repository and licensed
CC BY-NC 4.0. The dataset is not redistributed.
