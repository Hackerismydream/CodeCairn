# LoCoMo Evaluation Reuses a Verified Immutable Corpus

## Status

Accepted. Shared corpus publication, stable truth/index fingerprints, frozen
query vectors, retrieval-only runs, and cross-variant identity checks are
implemented. Process resource telemetry, watchdog enforcement, and the public
evidence-bundle migration remain required before publishing a full result.

## Context

The current LoCoMo runner treats ingestion and question evaluation as phases of
one variant run. `episode-only`, `hierarchy-no-neighbors`, and `hierarchy`
therefore each build an independent runtime even though they use the same
dataset, projection rules, document embedding model, and indexed documents.
Only the recall policy differs.

The frozen 200-question diagnostic currently projects 5,882 source memories
into 11,764 indexed documents: 5,882 Episodes and 5,882 AtomicFacts. The three
existing variant artifacts have equal document counts, content fingerprints,
and vector fingerprints. Repeating that ingestion consumes embedding quota,
wall-clock time, disk space, and native-memory headroom without adding
experimental independence.

EverOS was consulted as a mechanism-level reference. Its LoCoMo workflow can
ingest once and use `--skip-add` for subsequent retrieval modes. That is the
right experimental boundary, but a mutable implicit store plus a skip flag is
not a sufficient evidence contract for CodeCairn. A scored run must prove which
corpus it consumed, reject an incomplete or incompatible corpus before paid
inference begins, and remain independently resumable.

The benchmark also repeats the same query embedding once per variant. This is
cheap relative to document ingestion and answer judging, but freezing it makes
the retrieval ablation more reproducible and removes another provider call
from resumed runs.

Temporal neighbor expansion is an experimental enrichment, not part of the
minimum hierarchy contract. The previous implementation reported roughly 520
neighbor-expansion events per query before reranking and was both slower and
slightly less accurate than `hierarchy-no-neighbors`. ADR 0016 bounds rendered
expansion to 20 snippets after reranking. That removes the multiplicative cost
shape but does not establish a quality benefit. The benchmark must be able to
select the no-neighbor hierarchy when temporal enrichment does not earn its
cost.

## Decision

LoCoMo evaluation is split across two deep modules with one narrow artifact
seam:

1. `LoCoMoCorpusBuilder` projects and indexes the selected conversations, then
   atomically publishes a verified `LoCoMoCorpusArtifact`.
2. `LoCoMoEvaluationRunner` opens that artifact through a read-only recall
   interface and evaluates questions without owning ingestion.

A scored ablation builds one corpus and lets all recall variants consume the
same immutable artifact sequentially. A separately frozen
`LoCoMoQueryVectorSet` supplies query vectors to every variant. Each variant
still owns its answer, judge, retrieval-diagnostic, and checkpoint artifacts.

The old combined `all` execution path may remain for synthetic tests and local
smoke runs. It is not an accepted path for published benchmark evidence.

## Module Boundaries

### Corpus construction module

The corpus construction module owns:

- dataset and question-selection validation;
- conversation projection into Episode and AtomicFact truth;
- document embedding and index draining;
- per-conversation resumable ingest checkpoints;
- truth/index consistency verification;
- corpus manifest creation and atomic publication.

Its public interface is conceptually:

```python
class LoCoMoCorpusBuilder(Protocol):
    def build(self, request: CorpusBuildRequest) -> LoCoMoCorpusArtifact: ...
```

`CorpusBuildRequest` contains the dataset identity, selected conversation IDs,
projection contract, embedding identity, and output root. It does not contain
a recall mode, answer model, judge model, or question-level concurrency.

### Evaluation module

The evaluation module owns:

- verifying corpus and query-vector compatibility before any provider call;
- selecting and ordering questions;
- retrieving Recall Context under one declared recall policy;
- answer generation and judge voting;
- missing-only question resume;
- per-run reports and comparison inputs.

Its public interface is conceptually:

```python
class LoCoMoEvaluationRunner(Protocol):
    def run(self, request: EvaluationRunRequest) -> LoCoMoRunArtifact: ...
```

`EvaluationRunRequest` requires corpus and query-vector artifact references.
It cannot silently build a corpus or fall back to online query embedding.

### Read-only recall seam

Scored evaluation receives a `ConversationMemoryReader` interface exposing
only recall and diagnostics. The adapter does not expose memorize, cascade,
rebuild, queue-drain, or delete operations. SQLite is opened read-only where
supported. Stores without a reliable read-only mode are protected by both the
restricted interface and pre/post artifact verification.

This seam makes the ownership rule explicit: the corpus builder may mutate a
building directory; an evaluation run may only read a published corpus.

### Frozen query-vector adapter

`FrozenQueryEmbeddingAdapter` implements the existing query-embedding
interface from a `LoCoMoQueryVectorSet`:

- `embed_query` returns the exact stored float32 vector for a matching query;
- `embed_documents` fails closed;
- a missing query, changed normalization payload, dimension mismatch, or model
  identity mismatch fails before retrieval;
- there is no network fallback.

The adapter reports the original embedding and index identities so existing
compatibility checks continue to describe the vectors that actually built the
corpus.

## Corpus Artifact Contract

A corpus is first written under a non-consumable building name and is published
only after all verification gates pass:

```text
benchmark_results/locomo/corpora/
  .building-<build-id>/
    manifest.json
    checkpoints/ingest/<conversation-id>.json
    runtime/<conversation-id>/...
  corpus-<content-sha-prefix>/
    manifest.json
    checkpoints/ingest/<conversation-id>.json
    runtime/<conversation-id>/...
```

An interrupted `.building-*` directory is resumable. It is never accepted by
the evaluation module. Publication uses an atomic rename within one output
filesystem after the manifest is marked complete and fsynced.

The corpus manifest records at least:

- schema version and artifact kind;
- artifact ID, status, creation time, and generator repository commit;
- canonical build-contract SHA-256;
- dataset source, license reference, SHA-256, and selected conversation IDs;
- selection ID and selection SHA-256 when a frozen selection is used;
- projection and schema revisions for Episode and AtomicFact;
- complete embedding identity: adapter, source, model, revision, dimension,
  query/document mode contract, and index identity;
- per-conversation source-memory count, indexed-document count, content
  fingerprint, vector fingerprint, and ingest-checkpoint digest;
- aggregate source-memory and indexed-document counts;
- a canonical row-level vector digest ordered by durable document ID and
  computed from float32 vector bytes;
- index-queue counts and rebuild-consistency result;
- corpus content SHA-256.

Timestamps, temporary names, absolute paths, and machine-specific metadata are
excluded from content identity. The corpus content SHA-256 is derived from a
canonical representation of the stable fields, not from directory bytes or
SQLite/LanceDB file layout.

Before opening a corpus, the evaluation module verifies:

1. the manifest schema is supported and status is `complete`;
2. the content SHA-256 recomputes exactly;
3. the dataset, selection, projection, and embedding contracts match the run;
4. every declared conversation root and checkpoint exists;
5. no index job is pending, leased, failed, or stale;
6. truth and index document counts and fingerprints agree;
7. the query-vector artifact is compatible with the corpus embedding identity.

After each variant, the stable corpus fingerprints are checked again. Any
mutation invalidates that run and prevents cross-variant comparison.

## Query Vector Artifact Contract

The query-vector set is immutable and tied to a frozen question selection and
the corpus embedding contract:

```text
benchmark_results/locomo/query-vectors/
  queries-<content-sha-prefix>/
    manifest.json
    vectors.jsonl
```

Each canonical JSONL record contains:

- durable question ID;
- query role, so a later planner may freeze more than one derived query for a
  question without changing the artifact seam;
- SHA-256 of the exact normalized query payload passed to the adapter;
- float32 vector encoded as canonical base64 `f32le` bytes;
- vector dimension.

Question text does not need to be redistributed. The manifest records the
dataset SHA-256, selection ID and SHA-256, adapter/model/revision identity,
normalization and query-instruction contract, vector count, per-record digest,
and aggregate content SHA-256.

The 200-question diagnostic creates one vector set and reuses it across all
three variants. The 1,540-question full evaluation creates a separate vector
set after a winning recall policy has been selected.

## Run Artifact Contract

Each recall variant has its own run directory:

```text
benchmark_results/locomo/runs/<run-id>/
  manifest.json
  checkpoints/questions/<question-id>.json
  recall-sidecars/<question-id>.json
  summary.json
  resource-usage.json
```

The run manifest records, by content address rather than an implicit path:

- corpus artifact ID, corpus content SHA-256, and build-contract SHA-256;
- query-vector artifact ID and content SHA-256;
- question selection identity;
- recall mode and full retrieval-configuration hash;
- answer and judge provider/model identities and judge-vote count;
- repository commit and benchmark protocol revision;
- process-resource limits and observed termination state.

Question checkpoints are append-only and missing-only on resume. Existing
successful checkpoints are not overwritten. Failed or infrastructure-error
records remain evidence and are not counted as scored answers.

The ablation comparison verifier rejects variants unless all of them have the
same corpus content SHA-256, query-vector content SHA-256, question-selection
SHA-256, answer model, judge model, judge-vote count, scoring contract, top-k,
reranker identity, and shared retrieval budgets. Only recall-policy fields
declared by the frozen variant matrix may differ.

## Evaluation Ladder

### Stage 0: offline preflight

No provider call is allowed until preflight verifies configuration, dataset
hashes, artifact output containment, free disk, corpus compatibility, query
selection, expected request counts, and maximum cost envelopes. Preflight
prints the planned paid-call counts and exits non-zero on drift.

### Stage 1: 20-question retrieval canary

The canary selects five questions from each of the four scored categories by a
frozen deterministic SHA-256 rule. It runs retrieval only for all three recall
variants against the same corpus and query vectors. It does not call the
answer or judge model.

The canary passes only when:

- all 60 retrievals complete with no validation or infrastructure failure;
- every variant reports the expected corpus and query-vector identities;
- every question has a finite score set and a complete Recall Sidecar;
- hierarchy retrieval p95 is at most 5,000 ms and maximum latency is at most
  15,000 ms;
- maximum resident set size is at most 512 MiB as a soft gate and never exceeds
  the 768 MiB hard-stop limit;
- post-run corpus fingerprints are unchanged.

Failure stops the ladder before any 200-question answer or judge calls.

### Stage 2: frozen 200-question ablation

The diagnostic keeps 50 questions from each scored category and evaluates:

1. `episode-only`;
2. `hierarchy-no-neighbors`;
3. `hierarchy`, with the global post-ranking 20-snippet neighbor budget.

Variants execute sequentially in separate processes. Retrieval uses one worker
to keep latency measurements interpretable. Answer and judge calls may use the
frozen bounded concurrency of ten. The three-variant run therefore performs
document ingestion once, query embedding once per question, and independent
retrieval/answer/judge work per variant.

The core hierarchy is promoted only when:

- every variant has exactly 200 scored questions and zero infrastructure
  failures;
- `hierarchy-no-neighbors` improves overall accuracy over `episode-only` by at
  least 2.0 percentage points;
- the selected winner has retrieval p95 at most 2,500 ms;
- all artifact-identity and corpus-immutability checks pass.

Temporal neighbor enrichment is promoted over `hierarchy-no-neighbors` only
when all of the following hold:

- overall accuracy does not decline;
- temporal or multi-hop category accuracy improves;
- retrieval p95 increases by no more than 20 percent;
- maximum RSS remains below the hard-stop limit.

Otherwise `hierarchy-no-neighbors` is the selected hierarchy. A one-question
difference in this diagnostic is 0.5 percentage points, so the report includes
paired per-question outcomes and a paired bootstrap confidence interval. Small
deltas are reported as inconclusive rather than as a product claim.

If the hierarchy fails the 2.0-point core gate, the full run is blocked. The
benchmark remains useful as a diagnostic artifact; it is not rewritten or
hidden.

### Stage 3: 1,540-question full evaluation

Only the recall policy selected by Stage 2 is run on the full scored question
set. The other variants are not repeated at full scale by default. The frozen
200-question ablation provides the controlled comparison; the full run provides
the selected system's absolute score and category breakdown.

A second full-scale baseline requires a separate protocol revision and an
explicit evidence need. It is not part of the default cost envelope.

## Resource and Cost Gates

The following are launch limits, not expected performance claims:

| Stage | Soft target | Hard gate |
|---|---:|---:|
| Corpus document embedding | CNY 1.5 | CNY 2.0 |
| Corpus build wall time | 20 min | 30 min |
| Retrieval canary wall time | 10 min | 15 min |
| 200-question ablation paid inference | CNY 6 | CNY 10 |
| 200-question ablation wall time | 90 min | 120 min |
| Full selected-policy paid inference | CNY 18 | CNY 25 |
| Any process maximum RSS | 512 MiB | 768 MiB |

Cost preflight uses provider-reported or locally measured token counts and the
pinned price snapshot recorded in the run manifest. A hard estimate breach
prevents launch. Runtime accounting stops scheduling new paid work before the
cap can be exceeded; in-flight calls are allowed to finish and are preserved.

Corpus construction, query-vector construction, and every recall variant run
in separate processes. Variants are sequential, not concurrent. A watchdog
terminates a stage after ten minutes without a new durable checkpoint. This
limits native-memory accumulation and makes failure boundaries observable.

The current planning estimate is approximately 2.5 to 2.8 million provider
embedding tokens. At the supplied price snapshot of CNY 0.5 per million input
tokens, one canonical build is approximately CNY 1.3 to 1.4. Rebuilding it for
three variants would be roughly CNY 4.0 to 4.2 before query vectors. Provider
usage replaces this estimate in the final artifact; the design removes the
repeated cost either way.

## Failure and Recovery

- **Embedding quota, rate, or transport failure:** keep the unpublished
  building directory and resume only missing conversations. Never publish a
  partially indexed corpus.
- **Corpus mismatch or corruption:** fail before answer and judge calls. A run
  never silently rebuilds or repairs its input corpus.
- **Query-vector miss or mismatch:** fail closed before retrieval. Do not fall
  back to the online embedding provider.
- **Answer or judge interruption:** preserve completed question checkpoints and
  resume missing questions only.
- **Resource hard-stop:** mark the stage failed, flush resource telemetry, and
  retain all completed checkpoints for diagnosis.
- **Stalled progress:** stop after the watchdog interval, preserve the last
  checkpoint, and require an explicit resume.
- **Corpus mutation:** invalidate the affected run and all comparisons that
  reference its post-mutation identity.

Failure artifacts remain distinguishable from scored artifacts. Provider or
infrastructure failure is never converted into an incorrect answer, a zero
score, or a successful benchmark report.

## Evidence Bundle Changes

The public evidence bundle includes the corpus manifest and query-vector
manifest once, followed by content-addressed references from each run. It does
not duplicate ingest checkpoints under every variant.

The bundle verifier recomputes stable manifest digests, validates all run
references, proves cross-variant input identity, checks question/checkpoint
counts, and confirms that public reports do not redistribute protected LoCoMo
question text. Existing evidence code that assumes ingestion lives below a run
must migrate before a shared-corpus result can be published.

## Implementation Sequence

This ADR deliberately separates implementation from the first paid run:

1. Add artifact schemas, canonical digest helpers, and fake-store unit tests.
2. Add `LoCoMoCorpusBuilder`, resumable building directories, atomic publish,
   and a verifier using a deterministic fake embedding adapter.
3. Add the read-only corpus adapter and require corpus references for scored
   runs; update the evidence-bundle verifier.
4. Add `LoCoMoQueryVectorSet` and `FrozenQueryEmbeddingAdapter`, including
   fail-closed compatibility tests.
5. Add the retrieval-only canary, process resource telemetry, watchdog, and
   revised comparison gates.
6. Update the frozen protocol and operator documentation, then run offline
   tests, one synthetic smoke, the 20-question canary, and finally the
   200-question diagnostic in that order.

No 1,540-question run is launched until the 200-question artifact passes the
revised gates and a human verifies the cost preflight.

## Alternatives Rejected

### Keep one runtime per variant

This preserves physical isolation but repeats identical paid ingestion and
allows unnoticed corpus drift. Separate result directories already provide
experimental isolation when the input artifact is immutable and verified.

### Reuse a mutable store with `--skip-add`

This saves calls but leaves corpus identity implicit and permits a later run to
observe mutations. It is useful as an upstream mechanism reference, not as the
CodeCairn evidence contract.

### Copy the corpus directory for every variant

Copies avoid shared writes but add roughly one corpus-sized disk copy per
variant and make identity harder to prove. A read-only interface plus pre/post
fingerprint verification gives stronger evidence with less storage. A
filesystem snapshot may remain an emergency adapter for a store that mutates
during reads, but snapshots must share the same verified content identity.

### Run all variants concurrently

Concurrent variants obscure retrieval latency, multiply native-memory peaks,
and increase provider burst pressure. Sequential process isolation is slower
than unconstrained concurrency but produces interpretable measurements and a
bounded resource envelope.

### Promote temporal neighbors by allowing a one-point regression

An optional enrichment should not be accepted merely because it stays within
a negative tolerance. The feature must demonstrate category-specific benefit
without reducing overall accuracy and within a declared latency budget.

## Consequences

- LoCoMo document ingestion and embedding occur once per corpus contract, not
  once per recall variant.
- Query embedding occurs once per frozen question set, improving replay
  determinism and reducing provider dependence.
- Recall variants remain independently resumable and comparable because their
  results are separate while their inputs are content-addressed and identical.
- The full run becomes a promoted-system measurement rather than an expensive
  repetition of the entire ablation.
- Artifact verification and evidence-bundle code become more complex, but the
  additional complexity sits at one explicit seam instead of leaking through
  recall and scoring code.
- Temporal neighbors remain available for research but are not part of the
  default hierarchy unless the frozen diagnostic proves their value.
