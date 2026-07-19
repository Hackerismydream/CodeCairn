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
       BM25 union vector candidates
                    |
                 rerank
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
`codecairn.bootstrap` is the single composition root that selects the Codex,
filesystem, and SQLite adapters. Import-linter contracts prevent service and
entrypoint code from reaching through those ports to concrete adapters.

The evaluation module exposes one interface:

```text
run_suite(suite_manifest) -> EvaluationArtifact
```

Each run has an isolated workspace and immutable manifest. LoCoMo is a benchmark
adapter over the memory interface; coding-task A/B is a separate agent execution
adapter over the evaluation interface.

## Storage

- Markdown is immutable durable truth. Creation uses a same-directory temp
  file, flush, fsync, and an atomic create-if-absent link; an existing memory
  ID is never overwritten with different evidence.
- An evidence `source_path` is the observed import-time locator, while raw-event
  hashes are its immutable identity. The Import Ledger records every observed
  source location; recovery may resolve or repair live locators without
  rewriting memory identity.
- SQLite owns transactions, cursors, audit, leases, and the index outbox.
- LanceDB owns vector and lexical search material only. It is disposable.

## Reference policy

Pythia is a private prototype and regression corpus. CodeCairn may port a small
module only after its behavior is captured by an independent contract test.
EverOS is consulted for mechanisms and invariants such as atomic Markdown,
storage recovery, and LoCoMo orchestration. CodeCairn does not copy EverOS's
product surface or package structure.
