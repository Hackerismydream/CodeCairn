# EvidenceBundle Recall v2

## Status

Proposed on 2026-07-21. This document records an architecture decision and an
implementation/evaluation plan. It does not claim that Recall v2 is implemented
or that a new benchmark has been run.

## Decision Summary

CodeCairn will keep its existing small caller-facing recall interface and place
two deep modules behind the lifecycle boundary: `ProjectionBuilder` owns
ingest/rebuild work, while `RecallEngine` owns query-time recall.

Recall v2 will:

1. project a real source TaskEpisode or conversation segment into one Episode
   parent with multiple AtomicFact children;
2. add rebuildable entity and timeline postings in SQLite, without adding a
   graph database or another content truth;
3. retrieve lexical/vector Episode and AtomicFact seeds, then perform one
   budgeted entity/time expansion;
4. rerank compact evidence bundles and select them for query-anchor and
   relation coverage instead of rendering a flat top-20 document list; and
5. keep LoCoMo answer synthesis outside the memory runtime so retrieval gains
   and answer-prompt gains remain independently measurable.

Recall v2 does not use a query-time LLM. Markdown remains production durable
truth, SQLite remains state and rebuildable postings, and LanceDB remains a
disposable lexical/vector index. Evaluation corpora use an immutable,
content-addressed normalized corpus artifact as their declared projection
source.

## Why the Current Result Is Not Enough

The accepted full artifact
`locomo-full-deepseek-v4-flash-20260721-hierarchy-b4-v18` scored 1,074 of
1,540 questions, or 69.7403%, with no infrastructure failures.

| Category | Accuracy | All gold evidence in context | Accuracy when all evidence is present |
|---|---:|---:|---:|
| Multi-hop | 44.68% | 42.20% | 69.75% |
| Temporal | 66.67% | 87.54% | 73.31% |
| Open-domain | 44.79% | 50.00% | 47.83% |
| Single-hop | 82.16% | 90.96% | 88.63% |

A read-only audit aligned the 1,540 checkpoints with the dataset evidence IDs.
Four open-domain questions with no declared evidence were excluded from the
coverage denominator. Three malformed evidence references that cannot resolve
to a source turn are conservatively counted as missing. Across the remaining
1,536 questions:

- 90.17% included at least one gold evidence item;
- 78.84% included every resolvable gold evidence item and contained no
  unresolved reference;
- answers were correct 81.67% of the time when all evidence was present;
- answers were correct 31.03% of the time with partial evidence; and
- answers were correct 17.88% of the time with no evidence.

The 466 wrong answers separate into 124 with no gold evidence, 120 with partial
evidence, and 222 with all gold evidence already present. Fixing only the 124
pure retrieval misses could raise the score to at most 77.79%. Even magically
fixing every incomplete-context error would stop around 85.6%. A credible path
to 90 therefore requires both evidence-chain recall and evidence-grounded
answer synthesis.

The resource profile also argues against wider flat retrieval:

- every question rendered 20 ranked memories and consumed the global
  20-neighbor budget;
- recall context was about 18,107 characters at P50 and 21,517 at P95;
- answer input was about 6,236 tokens at P50 and 7,157 at P95;
- retrieval latency was 1,292 ms at P50 and 1,635 ms at P95;
- accepted max process RSS was 858,603,520 bytes (818.83 MiB) under the v18
  1 GiB hard gate.

Longer context correlated with lower accuracy in the audited Multi-hop,
Temporal, and Single-hop slices. This is not causal proof, but it is sufficient
evidence against increasing `top_k`, candidate multipliers, or neighbor windows
as the primary strategy.

## EverOS Reference Boundary

The published EverMemOS paper reports 93.05% on LoCoMo with a GPT-4.1-mini
backbone and 86.76% with GPT-4o-mini. The complete 93.05% system uses Qwen3
4B-class embedding/reranking, MemScenes, top-10 scene and Episode retrieval,
and a second query-rewrite round for a reported subset of questions. It
attributes the result to episodic trace formation, semantic consolidation, and
reconstructive recollection rather than to one retriever in isolation. See the
[EverMemOS paper](https://arxiv.org/pdf/2601.02163).

That result is not directly comparable to CodeCairn's DeepSeek v4 Flash answer
model, three-vote judge, prompts, retrieval budget, and local artifact contract.
The local EverOS checkout contains benchmark scripts and documentation but no
result artifact that proves a same-protocol 90% run.

Mechanisms worth adapting from the inspected EverOS code are:

- independent Episode sparse/dense and AtomicFact sparse/dense recall;
- AtomicFact MaxSim lifting to the Episode parent;
- rank-based fusion across heterogeneous retrieval lanes;
- OR-mode BM25 and one query vector reused across dense lanes;
- selecting parents before loading bounded child details;
- a fact/detail-oriented reranking instruction, while recognizing that the
  inspected agentic path reranks Episode text rather than fact bundles; and
- sufficiency/refinement callbacks as a later research reference, not a v2
  dependency.

Mechanisms that are intentionally excluded are:

- full-owner scans and 100,000-child sentinel candidate pools;
- query-time cluster traversal as the default path;
- a graph database or persisted pairwise entity clique;
- the corpus-specific LR calibration that lets a child evict its parent;
- OME, profile, skill, case, and reflection product surfaces; and
- an EverOS-specific package or API shape.

The reference is mechanism-level. CodeCairn keeps its own contracts,
provenance, storage ownership, runner, and evidence artifacts.

The inspected reference is pinned to EverOS commit
`b7d15f72527b8850b712838a46b13d4dd0f8d214`. Its ranking implementation also
depends on `everalgo-rank==0.4.1`; the locked wheel SHA-256 is
`675a8189d9ae3824c76d21d9bc409e0c62a3fc73023d02747bcff0b3982a2c92`.
Future comparisons must pin both identities because the repository wrapper
does not contain the complete ranking implementation.

## Current Root Cause

The current LoCoMo projection contract is `locomo-turn-memory-v1`:

```text
5,882 turns
  -> 5,882 CodingMemory files
  -> 5,882 Episode documents
  -> 5,882 AtomicFact documents
```

Each parent and child contain nearly the same single turn. The hierarchy is
therefore duplicate indexing rather than one meaningful Episode with multiple
facts. Sibling expansion has no useful fact siblings, and chronological
expansion can only add adjacent turns after ranking. It cannot assemble facts
linked by a person, place, symbol, event, or time range across sessions.

The current cue router also routes 1,325 of 1,540 questions to `fact_first` and
only changes the 40/20 candidate budgets. It does not produce subqueries,
entity anchors, temporal operators, or evidence coverage requirements. Four-way
RRF still answers only "which parents rank highly", not "which distinct facts
are jointly required to answer this query".

## Public Module and Interface

The ordinary caller interface remains source-compatible in method shape,
return type, and default values:

```python
class RecallEngine:
    def recall(
        self,
        query: str,
        *,
        repo_key: str,
        limit: int = 5,
    ) -> RecallResult: ...
```

```python
@dataclass(frozen=True)
class RecallResult:
    markdown: str
    sidecar: RecallSidecar
```

CLI, HTTP, Coding Agent consumers, and the LoCoMo adapter use this same
interface. Callers do not choose hierarchy levels, routes, expansion kinds,
reranker batches, or neighbor budgets.

`RecallSidecar` gains defaulted, backward-compatible fields for bundle summaries,
relations, per-stage trace data, `completion`, and `degraded_stages`. The
internal `EvidenceBundle` representation is not a public caller DTO. Existing
callers may continue to consume only `markdown` and the current sidecar fields;
no source or evidence identifier is dropped by the compatibility mapping.

The composition root configures a private budget:

```python
@dataclass(frozen=True)
class RecallBudget:
    max_candidates: int = 96
    max_rerank_bundles: int = 32
    max_bundle_tokens: int = 512
    max_rerank_tokens: int = 16_384
    max_expanded_facts: int = 24
    max_context_tokens: int = 4_000
    cooperative_timeout_ms: int = 5_000
```

Evaluation policies may freeze different budgets in a manifest, but benchmark
code cannot bypass the engine or inject question categories into the request.

Two lifecycle modules preserve depth without creating a god object:

- `ProjectionBuilder.build(source) -> ProjectionManifest` hides segmentation,
  annotation, projection, posting construction, and parity verification; and
- the existing `RecallEngine.recall(...) -> RecallResult` hides query sketching,
  candidate lanes, parent lifting, link expansion, reranking, coverage
  selection, and context compilation.

This separation gives leverage because the same projection/recall contracts
serve conversational and coding memories, and locality because build-time and
query-time policy do not leak into one another or into callers.

## Projection v2

### Projection source and manifest

`ProjectionBuilder` consumes one declared authoritative source and publishes a
content-addressed manifest:

```python
class ProjectionBuilder:
    def build(self, source: ProjectionSource) -> ProjectionManifest: ...
```

- production uses accepted CodingMemory Markdown artifacts as
  `MarkdownProjectionSource`;
- LoCoMo uses an immutable normalized conversation corpus artifact as
  `EvaluationCorpusProjectionSource`; and
- both sources expose stable content digests, source Episode identities, facts,
  and immutable evidence references.

The manifest records source digest, segmenter/annotator/normalizer revisions,
every projected document digest, and every posting digest. Rebuild parity means
that the same declared source and accepted annotation artifacts reproduce the
same logical IDs and content digests; it does not depend on LanceDB or SQLite
file bytes.

Publication uses immutable generations rather than attempting a cross-store
transaction. `ProjectionBuilder` writes one inactive `generation_id` into every
Lance row and SQLite posting, verifies both stores against the manifest, then
atomically switches one SQLite active-generation pointer. Recall pins that
active generation at request start and applies it to every Lance and SQLite
query. A missing row, mixed generation, or manifest mismatch fails with
`index_not_ready`; Recall never joins Lance generation N with SQLite generation
N-1. Old generations are garbage-collected only after they are inactive.

In production, any non-deterministic semantic annotation accepted by Evidence
Gate is written into the canonical CodingMemory Markdown before projection
publication; no second production content truth is created. In evaluation, the
accepted annotation is stored in the versioned, content-addressed normalized
corpus artifact. Rebuild replays the relevant authoritative source and never
calls the provider again. If accepted annotations are not persisted through
that source, the semantic annotator is disabled and its output is excluded from
the v2 MVP and parity claim.

### Episode and AtomicFact shape

Recall v2 changes the disposable search projection to:

```text
source TaskEpisode or semantic conversation segment
  -> EpisodeDoc
       |- AtomicFactDoc 1
       |- AtomicFactDoc 2
       `- AtomicFactDoc N
```

Stable identities are derived from immutable scope and source identities:

```text
EpisodeDoc ID    = hash(repo_key, source_episode_id, immutable_segment_id)
AtomicFactDoc ID = hash(EpisodeDoc ID, source_fact_id or source_span_digest)
```

Projection revisions belong in `ProjectionManifest` and per-document content
digests, not in logical identity. A projection upgrade may change content and
generation while preserving stable parent-child identity.

For production coding memory, all accepted CodingMemory artifacts sharing a
TaskEpisode contribute to one rebuildable Episode projection. For LoCoMo, the
corpus builder maps source sessions into the same Episode/AtomicFact contracts.
It may split a long session at semantic boundaries, but it cannot use questions,
gold answers, gold evidence IDs, or benchmark categories.

The first migration can use source-session grouping. With the current corpus,
that would produce roughly 272 Episode rows plus 5,882 fact rows instead of
11,764 near-duplicate rows, a reduction of about 47.7%. A later semantic
segmenter may create more than 272 Episode rows while preserving the essential
one-parent-to-many-facts invariant.

### Evidence-preserving fact schema

```python
@dataclass(frozen=True)
class AtomicFactProjection:
    fact_id: str
    episode_id: str
    text: str
    entity_mentions: tuple[EntityMention, ...]
    event_time: FactTime | None
    source_refs: tuple[EvidenceRef, ...]
```

```python
@dataclass(frozen=True)
class FactTime:
    observed_at: datetime | None
    source_order: int
    raw_expression: str | None
    resolved_start: datetime | None
    resolved_end: datetime | None
    precision: str | None
```

An optional ingestion-time semantic annotator may propose atomic facts,
aliases, and relative-time normalization. Evidence Gate still resolves every
proposal to immutable source spans. Accepted production output is committed to
canonical Markdown; accepted evaluation output is committed to the normalized
corpus artifact. Entity and time annotations are search metadata; they cannot
become unsupported evidence.

### Lightweight link projection

SQLite gains rebuildable posting tables rather than pairwise graph edges:

```text
entity_mentions(
  repo_key,
  entity_key,
  entity_kind,
  document_id,
  fact_id,
  surface,
  extractor_revision
)

timeline_entries(
  repo_key,
  entity_key,
  episode_id,
  document_id,
  fact_id,
  occurred_start,
  occurred_end,
  precision,
  source_order,
  normalizer_revision
)
```

Query-time joins derive `same_entity`, `entity_intersection`, `before`, and
`after` relations. The system never materializes an O(n^2) entity clique. Both
tables are deleted and rebuilt with the Lance projection from the declared
`ProjectionSource`: Markdown in production and the immutable normalized corpus
artifact in evaluation.

Coding entities include file paths, modules, symbols, commands, error
signatures, tests, and issue IDs. Conversation entities include speakers,
people, places, named events, and conservative aliases. The mechanism is shared;
only the source annotator differs.

## Recall Pipeline

```text
query + repo_key + limit
  -> deterministic QuerySketch
  -> one shared query embedding
  -> Episode BM25 + Episode vector
  -> AtomicFact BM25 + AtomicFact vector
  -> fact MaxSim-to-parent + rank fusion
  -> small seed set
  -> one bounded entity/time/provenance expansion
  -> EvidenceBundle construction
  -> fact-aware CrossEncoder rerank
  -> coverage-aware selection
  -> token-budget ContextCompiler
  -> RecallResult + enriched RecallSidecar
```

### QuerySketch

`QuerySketch` is a private value object, not a caller-facing classifier:

```python
@dataclass(frozen=True)
class QuerySketch:
    anchors: tuple[EntityKey, ...]
    temporal_op: Literal["none", "point", "duration", "order", "latest"]
    set_op: Literal["none", "union", "intersection"]
    wants_procedure: bool
    coverage_slots: tuple[CoverageSlot, ...]
```

It changes lane weights, expansion, and selection requirements but never hard
disables Episode or AtomicFact recall. For example:

```text
Which city have both Jean and John visited?
  anchors = [Jean, John]
  set_op = intersection
  slots = [Jean travel evidence, John travel evidence, shared city]
```

```text
Which file fixed the pytest timeout, and which command verified it?
  anchors = [pytest timeout]
  wants_procedure = true
  temporal_op = order
  slots = [failure, changed file, later verification]
```

Recall v2 stops here: it does not invoke a query-time planner. If deterministic
coverage remains insufficient, the sidecar reports the missing slots. A future
research adapter may test one bounded refinement call only after deterministic
v2 passes its quality and resource gates, under a separate configuration and
ablation identity.

### EvidenceBundle

The rerank and context unit becomes a compact evidence bundle rather than an
isolated turn or a full parent document:

```python
@dataclass(frozen=True)
class EvidenceBundle:
    episode_id: str
    focal_facts: tuple[FactExcerpt, ...]
    linked_facts: tuple[FactExcerpt, ...]
    relations: tuple[EvidenceRelation, ...]
    covered_slots: tuple[str, ...]
    token_cost: int
    score: float
```

Expansion is exactly one bounded join from the seed facts:

- `parent_child`: matched fact and source Episode;
- `same_entity`: another relevant fact sharing a normalized entity;
- `entity_intersection`: evidence connecting multiple query anchors;
- `before_after`: bounded endpoints in an entity or Episode timeline;
- `same_episode`: only the facts needed to restore local context; and
- `provenance`: exact source spans for every selected fact.

CrossEncoder processes at most 32 compact bundles in batches of four or eight.
Input text is capped per bundle. It does not receive every parent plus all facts
and neighbors, preventing one long candidate from creating the previous native
memory tail.

### Coverage-aware selection

After reranking, selection optimizes a declared objective instead of taking the
top scores verbatim:

```text
relevance
  + uncovered anchor bonus
  + uncovered relation/slot bonus
  + source diversity bonus
  - duplicate fact/Episode penalty
  - context token cost
```

The selector guarantees no particular answer. It only prevents twenty
near-duplicate facts about one anchor from displacing the second hop or the
other temporal endpoint. Every choice and rejection reason is recorded in the
trace.

### ContextCompiler

The compiler renders facts first, with a small Episode heading and explicit
relation labels:

```markdown
## Evidence bundle: payment callback duplicate submission

Why recalled: exact error match; same `PaymentService`; verified order

Timeline:
- 14:03 failed command ...
- 14:11 changed `payment/service.py` ...
- 14:16 `pytest tests/payment` passed ...

Sources:
- codecairn://memory/...
```

The structured bundles are canonical; Markdown is one view. Packing stops at a
global token budget, preserves complete provenance for retained facts, and
does not truncate evidence identifiers.

## Answer Synthesis Is a Separate Module

LoCoMo's 222 errors with all evidence present prove that retrieval alone cannot
reach the target. The evaluation adapter therefore owns a small
`EvidenceAnswerSynthesizer`:

```python
class EvidenceAnswerSynthesizer(Protocol):
    def answer(
        self,
        question: str,
        recall: RecallResult,
    ) -> GroundedAnswer: ...
```

```json
{
  "answer": "direct answer only",
  "supporting_evidence_ids": ["..."],
  "insufficient": false
}
```

The adapter may perform multi-evidence joins, interval arithmetic, and ordinary
world-knowledge inference when the evaluation protocol allows it. It must not
return the expected answer and then append a contradictory refusal. It does not
live in the production recall module, and prompt-only gains are reported
separately from retrieval gains.

A deterministic post-validator resolves every `supporting_evidence_id` against
the selected source references in `RecallResult.sidecar`. Unknown, duplicate,
out-of-scope, or structurally contradictory references invalidate the model
output. A non-insufficient answer must cite support, and answer text itself can
never create evidence.

Before another full run, an oracle-context diagnostic on a frozen
conversation-level development slice must measure the answer model and prompt
ceiling using gold evidence. If that ceiling is below 90%, no retrieval change
can honestly be presented as a path to 90% under the frozen answer protocol.
Prompt selection may use only that development slice. The final 1,540-question
run is reported as a standard-protocol confirmation, not as a fully unseen
holdout because it contains the development questions. A conversation-level
report slice is frozen from this design onward and receives no later per-item
tuning, but it is only a post-design confirmation: v18 and this audit have
already examined the full dataset. Truly unseen generalization requires a
different dataset or the separate coding-task A/B suite.

## Internal Seams and Adapters

The following seams have multiple real implementations and remain injectable:

- `RecallCorpus`: LanceDB + SQLite production adapter and in-memory test
  adapter;
- `EmbeddingPort`: DashScope production adapter and explicit fixed test
  adapter;
- `RerankPort`: pinned local CrossEncoder and deterministic test adapter;
- `MentionAnnotator`: deterministic coding/conversation annotators and an
  optional cached ingestion-time semantic annotator;
- `TokenizerPort` and `Clock` for reproducible budgets and traces.

`QuerySketch`, parent lifting, bundle construction, coverage selection, and
context compilation remain private implementation modules. Making each one a
public plugin would expose implementation churn and reduce module depth.

Hashing and fusion-only ranking remain explicit test adapters. Provider
failures never silently change production retrieval semantics.

## Invariants, Errors, and Performance Contract

### Invariants

- Every query is scoped before retrieval; post-retrieval repository filtering
  is forbidden.
- Every AtomicFact has exactly one Episode parent and at least one immutable
  source reference.
- Every emitted link joins existing grounded facts; links cannot invent facts.
- Fixed projection/index bytes, query vector, adapter outputs, configuration,
  and query produce stable ordering with the final tie-break
  `(score descending, source_order ascending, document_id ascending)`.
- Candidate, expansion, rerank-token, and context-token budgets are hard
  in-process limits.
- A deleted SQLite posting/Lance index rebuilds to 100% projection parity from
  the declared `ProjectionSource`.
- Degradation or timeout is explicit in `RecallSidecar`; there is no silent
  provider or policy fallback.

Provider name and declared revision do not imply deterministic output for a
provider-managed alias. Sidecars and scored artifacts therefore record the
query-vector digest and active index/manifest digest in addition to provider
identity. Replayable evaluation uses frozen query vectors; live runtime results
are deterministic only for identical adapter outputs.

### Errors

The caller-facing error surface remains small: current argument validation
continues to raise `ValueError`, and a new `RecallUnavailable` represents the
case where no contract-valid result can be produced. Stable reason codes such
as `scope_violation`, `index_not_ready`, `provider_unavailable`,
`dependency_timeout`, or `budget_exceeded` live in the exception payload and
sidecar rather than becoming a wide public exception hierarchy.

The composition root converts `cooperative_timeout_ms` into one absolute
monotonic deadline and propagates the remaining time through embedding,
reranking, SQLite, and Lance adapters. Network adapters set transport timeouts
from that deadline; local adapters check before and after non-cancellable
calls. The engine cannot claim to preempt a stuck native call. Evaluation
workers therefore add a separate process watchdog, initially 10 seconds, as
the hard wall-clock limit. If the cooperative deadline leaves valid base
evidence but prevents a later stage, the engine may return
`completion="partial"` and explicit `degraded_stages` in the sidecar. It raises
`RecallUnavailable` only when it cannot produce a valid result. The 2.5-second
P95 target is a soft SLO, below the 5-second cooperative deadline and 10-second
worker watchdog, so latency measurements are not truncated at the SLO.

### Initial budgets

- one query embedding shared by all dense lanes;
- at most 96 globally deduplicated candidates after the four retrieval lanes
  are fused and before documents are materialized for expansion/reranking;
- at most 32 rerank bundles;
- at most 512 tokens per rerank bundle and 16,384 rerank tokens in total;
- at most 24 expanded facts;
- CrossEncoder batch size four by default, eight only after memory profiling;
- 4,000 final context tokens, with a P50 target of 2,500-3,500;
- balanced P95 retrieval target below 2.5 seconds;
- cooperative request deadline at 5 seconds and evaluation worker hard
  watchdog at 10 seconds;
- soft process RSS target below 1 GiB and hard runner limit at 2 GiB; and
- no full-owner scan or unbounded breadth-first expansion.

## Design Considered Twice

### Alternative A: minimal EverOS-style hierarchy only

This option would keep the current projection and add calibrated parent/fact
eviction plus larger child recall. It is simple, but the current parent/fact
pair is already 1:1, and the multi-hop failure is missing chain coverage rather
than missing rank fusion alone. It does not solve cross-session entity or time
relations and is rejected as insufficient.

### Alternative B: EverOS-style agentic cluster recall

This option would add persistent semantic clusters, LLM query decomposition,
sufficiency checks, and repeated cluster-scoped traversal. It offers
flexibility but increases latency, provider coupling, nondeterminism,
operational state, and benchmark cost. It is rejected from v2.

### Alternative C: persistent graph database

This separate option would materialize entity/fact edges in a graph database
and run multi-hop graph traversal. EverOS does not require this mechanism, and
CodeCairn does not need another stateful service or O(n^2) edge materialization.
It is rejected in favor of SQLite postings and an ephemeral bounded join.

### Chosen design

EvidenceBundle Recall v2 combines the useful middle: a genuine parent/child
projection, local postings, one bounded deterministic expansion, and coverage
selection. It preserves the default caller simplicity of Alternative A while
retaining the multi-evidence leverage sought by Alternatives B and C.

## Implementation and Evaluation Sequence

No full benchmark is allowed during the architecture stage.

### PR1: Projection v2

- group source TaskEpisode/session memories under one Episode projection;
- preserve multiple grounded AtomicFacts per parent;
- add first-class source order, entity, and time fields;
- add `ProjectionSource` and content-addressed `ProjectionManifest` contracts;
- version the projection and rebuild contract; and
- prove source-to-index parity and repository isolation.

### PR2: entity and timeline postings

- add the SQLite projection tables;
- implement deterministic coding and conversation annotators;
- add bounded posting queries and alias-collision tests; and
- prove index deletion and rebuild consistency.

### PR3: RecallEngine v2

- retain the current caller interface and default values;
- implement QuerySketch, bounded seed lanes, link expansion, EvidenceBundle
  rerank, coverage selection, and token-budget compilation;
- extend the sidecar with bundle/trace/completion fields; and
- add resource, deadline-propagation, and partial-result contract tests.

### PR4: evaluation answer separation

- add the structured EvidenceAnswerSynthesizer;
- validate every cited evidence ID against selected source references;
- add provider-free evidence-coverage reporting;
- add the development-slice oracle-context answer-ceiling diagnostic; and
- freeze prompt-only and retrieval-only ablation identities separately.

### PR5: frozen conversation-level development ladder

Run only after PR1-PR4 pass local tests. Compare one change at a time:

1. current baseline;
2. real Episode grouping;
3. clause-level AtomicFacts;
4. QuerySketch and coverage selection;
5. entity posting expansion;
6. timeline expansion and interval normalization;
7. EvidenceBundle reranking;
8. optional persisted ingestion-time semantic annotation; and
9. fixed retrieval plus the answer synthesizer.

The development slice should contain about 200 questions while keeping whole
conversations together. If the existing frozen 200-question selection is not
conversation-disjoint, it remains historical evidence and a new selection ID
is published rather than overwritten. Before implementation, v18 is reduced
under that exact split to create separate dev/report baseline artifacts. The
new development gate is evaluated against its own baseline:

- infrastructure failures = 0;
- overall all-evidence coverage improves by at least 8 percentage points and
  reaches at least 85%;
- multi-hop all-evidence coverage improves by at least 20 percentage points and
  reaches at least 60%;
- single-hop all-evidence coverage regresses by no more than 1 percentage
  point;
- complete-evidence answer accuracy improves by at least 8 percentage points
  and reaches at least 88%;
- context-token P50 at most 3,500;
- retrieval P95 below 2.5 seconds;
- process RSS soft target below 1 GiB and hard limit at 2 GiB; and
- projection rebuild and repository isolation both 100%.

Only a configuration that passes every gate may advance to the 1,540-question
run. The first credible full-run target is at least 80%; at least 85% is a strong
recruiting result under the frozen DeepSeek protocol. Ninety percent remains a
stretch target that requires a protocol-matched answer-model ceiling and a
verified full artifact, not an architecture promise. The report must also show
the conversation-level report slice frozen from this design onward, disclose
that it is not historically unseen, and disclose that the standard 1,540 total
contains the development conversations.

## Benchmark Integrity and Non-goals

Recall v2 must not:

- route on LoCoMo category labels;
- use a question's gold answer or evidence IDs during ingest or recall;
- add synonyms, aliases, or date rules for previously observed failed
  questions;
- change embedding, answer model, judge, prompts, and retrieval policy in one
  ablation and attribute the total gain to retrieval;
- create a LoCoMo-only recall stack;
- report an external EverOS score as a CodeCairn result; or
- replace the coding-task memory-on/off experiment with LoCoMo.

LoCoMo measures the general recall and evidence-reasoning substrate. The coding
A/B suite remains the product-level proof that memory reduces repeated reads,
failed commands, token cost, or task failure for a Coding Agent.

## Intended Recruiting Evidence

After implementation and verified evaluation, the defensible project story is:

> Designed a lightweight evidence-linked recall runtime without a graph
> database or query-time LLM. A shared Episode/AtomicFact projection,
> SQLite entity/time postings, and coverage-aware EvidenceBundle selection
> serve both LoCoMo and Coding Agent recall through one stable interface.

The final resume may add LoCoMo accuracy, all-evidence coverage, P95 latency,
RSS, rebuild parity, and coding memory-on/off deltas only after those numbers
exist in immutable, independently verifiable artifacts.
