# Architecture

CodeCairn keeps a small public interface over a deep import and evidence
pipeline.

```text
Codex / Claude Code JSONL
          |
          v
 Provider Importer ---- raw evidence locator
          |
          v
      Agent Trace
          |
          v
 Task Episode segmenter
          |
          v
 deterministic Evidence Facts
          |
          +--> LLM semantic compression (untrusted proposal)
          |                    |
          +--------------------+
                    v
              Evidence Gate
                    |
          atomic Markdown write
                    |
          SQLite ledger + outbox
                    |
              Mini Cascade
                    |
       Episode -> AtomicFact projection
                    |
       soft parent/child query routing
                    |
       four-way BM25/vector candidate union
                    |
       fact-to-parent MaxSim + neighbor expansion
                    |
           CrossEncoder rerank
                    |
         Recall Context + sidecar
```

## Primary seams

The import module exposes one interface:

```text
import_session(source, repo_key) -> ImportResult
```

It hides parsing, stable identity, episode creation, evidence derivation,
durable writes, and cursor commits. CLI and HTTP call the same interface.

`MemoryRuntime` depends only on importer, Markdown-store, and state-store ports.
`codecairn.bootstrap` is the single composition root that selects the provider
router, filesystem, and SQLite adapters. The router reads a JSONL source once,
selects the Codex or Claude Code adapter, and keeps provider format branches out
of the service layer. Import-linter contracts prevent service and entrypoint
code from reaching through those ports to concrete adapters.

Semantic compression receives only fact identifiers, kinds, roles, and text
after an explicitly configured redactor runs. A configured byte limit is
checked before the remote-model port is called, and strict schema parsing keeps
model output from supplying evidence fields. The Evidence Gate resolves every
proposed fact identifier against the repository-scoped deterministic fact set.
Accepted memories persist both fact identifiers and complete deterministic fact
snapshots in Markdown and SQLite; accepted and rejected proposals both create
SQLite gate-audit rows with their proposal, resolved references, and reason.

Command results become verification facts only when deterministic command
classification identifies a test, lint, type-check, or build invocation.
Verified Fix requires that successful verification to occur after a file change
in the same Task Episode and source chronology. Debug Episode requires an
ordered user task, tool action, and observed success or failure from one
episode. A bounded, redacted JSONL export exposes gate candidates for human
precision labels without including raw evidence locators.

The evaluation module exposes one interface:

```text
run_suite(suite_manifest) -> EvaluationArtifact
```

Each run has an isolated workspace and immutable manifest. LoCoMo is a benchmark
adapter over the memory interface; coding-task A/B is a separate agent execution
adapter over the evaluation interface.

LoCoMo parallelizes only across conversations, whose runtime roots are already
isolated. Within one conversation, retrieval, answer generation, and repeated
judge calls retain deterministic order. Malformed structured judge output is
retried within the same vote with a deterministic attempt-specific prompt and
seed, up to the manifest-recorded attempt and response-length limits. Every
attempt is retained for audit and included in usage accounting. The checkpoint
contract is missing-only: resume validates the original manifest and fills
absent ingest or question artifacts without rewriting completed evidence.

The evidence-bundle reducer sits outside the runtime use cases. It copies only
public aggregate inputs, recomputes the four suite reports, derives inventory
counts, and generates recruiting copy from those values. A bundle SHA-256
inventory and deterministic verifier prevent a checked-in metric or resume
bullet from drifting away from its underlying artifacts.

CLI and HTTP depend on `CodeCairnApplication`, a shared use-case facade. The
composition root supplies the local runtime, evaluation dispatch, and
operational diagnostics. HTTP adds path authorization, request identifiers,
stable error envelopes, and a loopback-only bind policy; it does not implement
alternative import, recall, evaluation, or health behavior.

## Storage

- Markdown is immutable durable truth. Creation uses a same-directory temp
  file, flush, fsync, and an atomic create-if-absent link; an existing memory
  ID is never overwritten with different evidence.
- An evidence `source_path` is the observed import-time locator, while raw-event
  hashes are its immutable identity. The Import Ledger records every observed
  source location; recovery may resolve or repair live locators without
  rewriting memory identity.
- SQLite owns transactions, cursors, audit, leases, and the index outbox.
- Existing SQLite repositories migrate online with an empty `facts_json`
  default. Legacy Markdown without a fact snapshot remains readable and loses
  no memory-level data.
- Import checkpoints hash the stable event prefix and replay only the final
  active Task Episode. Markdown recovery is atomic, hash-verified, and recorded
  through resumable SQLite audit rows.
- Cascade workers reparse the committed Markdown artifact for both the parent
  narrative and fact snapshot; SQLite metadata cannot author AtomicFact index
  rows. Oversized Markdown is rejected before any durable file is created.
- LanceDB owns vector and lexical search material only. Each Coding Memory is
  projected into one Episode document and zero or more AtomicFact children.
  Parent identifiers and per-document digests are deterministic, and rebuild
  parity checks both memory groups and all projected documents. It is
  disposable.
- Production vectors and reranker scores come from local ONNX snapshots pinned
  to immutable Hugging Face artifact commits. Lance rows retain the logical
  alias, artifact source, commit, dimension, and FastEmbed-sensitive index
  identity. Changing any identity component re-embeds the projection under an
  inter-process operation lock before it can be queried. Query sidecars and
  evaluation manifests retain the effective model identities. Feature hashing
  and fusion-only ranking are explicit test Adapters, not production fallbacks.
- RecallPlanner searches Episode and AtomicFact documents independently. Its
  deterministic soft route changes candidate-pool sizes without disabling the
  secondary level. AtomicFact hits are max-pooled by parent before four-way RRF,
  and matched facts plus bounded same-Episode chronological neighbors are sent
  to the CrossEncoder and emitted with source-memory attribution. The retrieval
  manifest freezes `episode-only`, `hierarchy-no-neighbors`, or `hierarchy` so
  the 200-question diagnostic can measure each added layer.

## Reference policy

Pythia is a private prototype and regression corpus. CodeCairn may port a small
module only after its behavior is captured by an independent contract test.
EverOS is consulted for mechanisms and invariants such as atomic Markdown,
storage recovery, and LoCoMo orchestration. CodeCairn does not copy EverOS's
product surface or package structure.
