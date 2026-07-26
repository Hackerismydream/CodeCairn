# Evaluation Scope

CodeCairn evaluation establishes whether extraction is grounded, retrieval is
useful, storage is recoverable, and memory changes coding-task outcomes. It
does not turn controlled benchmark results into production-traffic claims.

## Relationship to the runtime

```text
immutable suite inputs
        |
        v
evaluation adapters
        |
        +--> ordinary runtime/service contracts
        +--> isolated workspaces and memory roots
        +--> provider and verifier adapters
        |
        v
immutable run artifacts
        |
        v
pure report reducers
        |
        v
public evidence bundle
        |
        v
offline verifier
```

Evaluation may compose Mini Cascade directly to create a complete indexed
corpus. That proves the service component and benchmark path; it does not prove
that the public CLI/server owns the same lifecycle.

## Suites

| Suite | Primary question | Published measurements |
|---|---|---|
| Retrieval | Can one historical labeled corpus retrieve relevant memories? | Recall@5, MRR, latency, isolation |
| Recovery | Do synthetic corruption, deletion, replay, and rebuild checks preserve truth? | Deterministic pass/fail checks and parity |
| CodingMemoryBench | Does supplied memory context improve controlled coding tasks? | Pass rate, tokens, actions, tool/file activity |
| LoCoMo | Can attributed conversations support long-context QA under the current frozen protocol? | Completion, accuracy, category results, usage |

LoCoMo exercises an end-to-end conversation-memory QA path, while retrieval and
coding isolate different failure boundaries. It is not an end-to-end test of
the public Codex/Claude import and cascade lifecycle. No single score
substitutes for the others.

### What each published suite does not prove

| Suite | Exercised path | Not exercised |
|---|---|---|
| Retrieval | Historical corpus -> index -> recall -> rebuild | Current DashScope/CrossEncoder composition; public trace import; public cascade lifecycle |
| Recovery | Two-memory synthetic truth/index failure fixtures | Production-scale recovery or provider failures |
| CodingMemoryBench | Checked-in pre-retrieved context -> isolated Codex task -> hidden verifier | Import, indexing, or retrieval |
| LoCoMo | Attributed Episode -> `write_episode` -> current retrieval -> answer/judge | Codex/Claude JSONL importer and public application facade |

No checked-in bundle currently proves the complete public
`Codex/Claude import -> index lifecycle -> recall` path.

## Artifact contract

Each run has an immutable identifier and explicit inputs. Newer suite schemas
bind the applicable subset of:

- repository commit;
- dataset, task, question selection, or corpus digests;
- workspace and memory snapshots;
- model, endpoint, declared revision, adapter, and reasoning configuration;
- seed, repeat, concurrency, resource, and timeout limits;
- raw event, verifier, checkpoint, attempt, token, latency, and cost
  observations.

Run directories are exclusive. Resume is missing-only and validates the
original manifest before filling absent checkpoints. Reports are pure readers
and never repair or rewrite runtime state.

The current v3 bundle combines artifacts created under different historical
schemas. Its bundle-level lock hash and environment describe the evidence
reducer checkout, not necessarily each source experiment. The older standalone
retrieval, coding, and recovery manifests record short source commits but do
not record their source lock or environment. The builder also does not
machine-prove that caller-supplied JUnit and coverage files came from the
declared clean commit.

## Provider and failure boundaries

- Model output is an untrusted answer or judgment, not provenance.
- Malformed answer/judge output follows the versioned retry contract and
  retains every attempt.
- Unknown provider spend fails closed in paid build paths.
- Infrastructure failures remain distinct from scored wrong answers.
- Exact LoCoMo repair may replace only the base run's complete infrastructure
  failure set; the negative base artifact remains immutable and visible.
- A live endpoint, configured key, or HTTP response is not by itself a
  successful scored run.

## Current public evidence

The current generated bundle is
[`../../evidence/benchmark-v3/README.md`](../../evidence/benchmark-v3/README.md).
Its offline verifier recomputes the suite reports, scale counts, generated
recruiting copy, and SHA-256 inventory without provider credentials.

Published headline values are:

| Measurement | Current evidence |
|---|---:|
| Retrieval Recall@5 | 96.00% |
| Retrieval MRR | 0.7979 |
| Retrieval P95 latency | 10.91 ms |
| Index rebuild consistency | 100.00% |
| Coding pass rate, memory off | 85.00% |
| Coding pass rate, memory on | 100.00% |
| LoCoMo formal scored-outcome coverage | 100.00% |
| LoCoMo category 1-4 answer accuracy | 82.60% |
| Automated tests captured by the bundle | 644 |
| Combined line/branch coverage captured by the bundle | 81.53% |

The bundle, rather than this table, is authoritative for values and raw inputs.
Known limitations include excluded adversarial category 5, pending provider
cost where traces expose none, controlled public coding tasks, and
single-machine latency.

The 96.00% Recall@5, 0.7979 MRR, and 10.91 ms P95 rows come from
`retrieval-fbc7023`. That historical retrieval manifest does not record model
identity and predates the current DashScope plus local CrossEncoder
composition. Those values must not be attributed to the current production
retrieval profile.

The LoCoMo composite gives all 1,540 selected questions a formal scored
outcome. Of those, 1,538 reached judging and two exhausted the frozen answer
contract and were scored wrong without judge votes. Generated bundle copy that
summarizes all 1,540 as receiving three judge votes should be read with this
qualification until the generator wording is corrected; generated bundle files
are not hand-edited.

The generated v3 label `Statement coverage` is also historical wording. The
underlying Coverage.py total is combined statement and branch coverage:
statements are 86.17%, branches are 69.11%, and the combined value is 81.53%.
The immutable bundle is not hand-edited; a future generated bundle must correct
the label.

## Offline verifier boundary

`codecairn evidence verify` verifies:

- the public file inventory and hashes;
- aggregate values recomputed from published query, outcome, recovery, coding,
  and quality records;
- exact-repair set and source consistency;
- regenerated README and resume copy against the checked-in bundle.

It does not:

- call an embedding, answer, judge, or coding-agent provider;
- re-judge semantic answer correctness;
- recreate hidden-verifier workspaces or execute their commands;
- prove that quality inputs came from the declared clean commit;
- prove the current public import-to-recall lifecycle.

Offline verification is artifact-integrity verification, not a full experiment
rerun. Provider-managed aliases, a coding agent without seed support, and
missing immutable provider revisions also prevent a claim of future
bit-for-bit or score-identical reproduction.

## CI boundary

Current CI runs `make check`, verifies the retained `benchmark-v1` bundle, and
builds package artifacts. It does not yet verify `benchmark-v3`. Until CI is
updated, release review must run:

```bash
uv run codecairn evidence verify evidence/benchmark-v3
```

Documentation must not describe the v3 bundle as CI-protected before that
workflow changes.

## Ownership

| Area | Owner |
|---|---|
| Suite input and execution contracts | `codecairn.evaluation` |
| Runtime behavior under evaluation | `codecairn.service` and runtime adapters |
| Immutable filesystem artifacts | Evaluation artifact helpers |
| Public redaction and aggregation | Evidence bundle reducer |
| Headline values | Generated evidence bundle |
| Interpretation and limitations | This scope and `docs/evidence-bundle.md` |

## Detailed references

- [`../evidence-bundle.md`](../evidence-bundle.md) — public build and verifier
  contract.
- [`../recall-v2-design.md`](../recall-v2-design.md) — historical Recall v2
  proposal and diagnosis.
- [`../../benchmarks/locomo/README.md`](../../benchmarks/locomo/README.md) —
  LoCoMo execution and spend gates.
- [`../../benchmarks/retrieval/README.md`](../../benchmarks/retrieval/README.md)
  — checked-in retrieval set.
- [`../../benchmarks/coding/README.md`](../../benchmarks/coding/README.md) —
  controlled coding tasks and hidden verifier.
- [`../adr/0037-locomo-provider-failures-use-exact-repair-runs.md`](../adr/0037-locomo-provider-failures-use-exact-repair-runs.md)
  and
  [`../adr/0039-public-evidence-publishes-exact-repair-outcomes.md`](../adr/0039-public-evidence-publishes-exact-repair-outcomes.md)
  — exact repair and public proof.
