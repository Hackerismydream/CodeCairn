# Runtime Operations

This document describes the public behavior that exists on the current main
branch. It separates implemented service components from entrypoint lifecycle
wiring so operators do not mistake queued durable memory for a searchable
index.

## Current support matrix

| Capability | CLI | HTTP | Current behavior |
|---|---|---|---|
| Import session | `codecairn import` | `POST /api/v1/import` | Normalizes the trace and persists deterministic Failed Command memories |
| List durable memory | `codecairn list` | `GET /api/v1/memories` | Reads committed SQLite memory state by `repo_key` |
| Recall | `codecairn recall` | `POST /api/v1/recall` | Searches the existing LanceDB projection |
| Diagnostics | `codecairn doctor` | `GET /api/v1/health` | Reports truth, ledger, queue, index parity, and provider configuration |
| Dedicated index sync/retry/rebuild control | Not exposed | Not exposed | Status is visible through diagnostics; Mini Cascade control exists only as a service composition seam |
| Evaluation run/report | `codecairn eval ...` | Evaluation run/report routes | Uses immutable explicit input/output roots |
| Evidence build/verify | `codecairn evidence ...` | Not exposed | Builds or verifies public evidence bundles |

## Product lifecycle on current main

```text
codecairn import
      |
      +--> Markdown ready
      +--> SQLite ledger committed
      +--> Index Queue pending
      |
      x    no public worker or sync command
      |
codecairn recall
      |
      +--> searches the existing LanceDB state only
```

Consequences:

- Import success proves durable truth and queue commit, not search readiness.
- Ordinary trace import does not automatically produce User Preference,
  Repository Convention, Verified Fix, Debug Episode, or Conversation Episode.
- A fresh runtime can contain one committed memory and one pending index job
  while LanceDB contains zero documents.
- `doctor` correctly reports this state as `degraded`.
- Recall can return `completion=partial`, `degraded_stages=["no_candidates"]`,
  and no ranked memories even when `list` returns durable memory.

Tests and evaluation code explicitly compose and run `MiniCascade`; ordinary
CLI and server startup do not. There is currently no supported operator
workaround on the public command surface. Internal Python helpers are not
documented as a stable product API.

## Required product acceptance gate

The public local loop is complete only when one supported lifecycle owns the
queue-to-index transition. The implementation may use a server worker, a
one-shot CLI command, or both, but it must satisfy this black-box contract:

```text
fresh root
  -> import supported fixture
  -> observe durable memory
  -> use supported index lifecycle
  -> doctor reports healthy index parity
  -> recall returns the imported memory with provenance
```

The lifecycle must also expose pending, leased, failed, stale, and indexed
counts; preserve atomic leases; retry without duplicating embeddings; and
rebuild both parent and child projections from Markdown.

Until that gate passes, documentation must describe CodeCairn as having
implemented runtime components and evaluation evidence, not as an
install-and-use complete memory product.

## CLI

Runtime commands:

```text
codecairn import SOURCE --repo-key REPO --root ROOT
codecairn list --repo-key REPO --root ROOT
codecairn recall TASK --repo-key REPO --root ROOT [--limit N]
codecairn doctor --root ROOT
```

`recall --format markdown` emits only the Markdown context. The default JSON
format includes both Markdown and the structured sidecar.

`doctor` always emits JSON and currently returns a successful process exit even
when its payload says `status="degraded"`. Automation must parse the status
field rather than rely on the exit code.

Evaluation commands:

```text
codecairn eval run ...
codecairn eval report ...
codecairn eval build-locomo-corpus ...
codecairn eval build-locomo-query-vectors ...
codecairn eval compare-locomo ...
codecairn eval promote-locomo ...
codecairn eval report-locomo-evidence ...
codecairn eval compose-locomo-repair ...
```

Evidence commands:

```text
codecairn evidence build ...
codecairn evidence verify BUNDLE_DIR
```

Use `codecairn --help` and the subcommand `--help` output for complete option
schemas. Evaluation protocol details live in
[`../evaluation/README.md`](../evaluation/README.md).

## HTTP

The six versioned routes are:

| Method | Route | Use case |
|---|---|---|
| `POST` | `/api/v1/import` | Import one supported session |
| `GET` | `/api/v1/memories` | List memory by repository namespace |
| `POST` | `/api/v1/recall` | Compile Recall Context |
| `POST` | `/api/v1/evaluations` | Run one evaluation suite |
| `GET` | `/api/v1/evaluations/{suite}/{run_id}` | Read an evaluation report |
| `GET` | `/api/v1/health` | Read operational diagnostics |

CLI and HTTP call the same `CodeCairnApplication` facade. HTTP adds request
validation, a stable error envelope, `x-request-id`, and path authorization;
it does not add a background index worker.

`POST /api/v1/evaluations` executes synchronously. It is not an asynchronous
job-submission API; a request may be long-running and may incur configured
provider cost.

## Runtime roots and path safety

The CLI defaults to `.codecairn`. The server uses:

| Variable | Default | Purpose |
|---|---|---|
| `CODECAIRN_RUNTIME_ROOT` | `.codecairn` | Markdown, SQLite, cache, and LanceDB root |
| `CODECAIRN_ARTIFACT_ROOT` | `artifacts` | Evaluation artifact root |
| `CODECAIRN_SOURCE_ROOTS` | current working directory | Allowed import/evaluation source roots |
| `CODECAIRN_BIND_HOST` | `127.0.0.1` | HTTP bind host |
| `CODECAIRN_PORT` | `8000` | HTTP port |

The server rejects non-loopback binds. Source and artifact inputs must remain
below configured roots after path resolution. Runtime state can contain source
paths, commands, and evidence text and is ignored by Git.

## Retrieval profiles

| Profile | Purpose | Boundary |
|---|---|---|
| `dashscope` | Default production embedding plus pinned local reranker | Requires an API key and network for embeddings |
| `fastembed` | Explicit offline local embedding plus pinned local reranker | Requires pinned artifacts in the configured model cache |
| `hashing-test` | Deterministic contract tests | Never a production fallback |

Index rows, sidecars, and manifests retain public provider identity fields but
never credentials. Changing endpoint, model, revision, dimension, or adapter
identity makes the existing projection incompatible and triggers the
rebuildable migration path when the index is operated.

## Diagnostics

`doctor` and `/api/v1/health` return separate sections:

- `markdown_truth`: parseability and memory count;
- `import_ledger`: imports, memories, observed events, audits, and pending
  recovery;
- `index_queue`: pending, leased, indexed, failed, and stale jobs;
- `index`: memory/document fingerprint parity and error type;
- `providers`: configuration availability without secrets.

`status=healthy` requires parseable Markdown, no pending recovery, zero pending,
leased, failed, or stale queue jobs, and exact memory/document parity. Provider
readiness is reported separately and is not proof that a live inference
request will succeed.

## Failure handling

- Do not delete Markdown to recover an index.
- Do not edit SQLite or LanceDB as memory truth.
- Do not treat direct edits to an existing gate-managed Markdown file as a
  supported workflow; current reconcile does not re-run its original gate.
- Do not claim recall readiness from import success alone.
- Preserve failed queue rows and their error types for diagnosis.
- Rebuild from Markdown when the disposable index is corrupt or incompatible.
- Treat a provider HTTP reachability result as connectivity evidence only, not
  as successful embedding or recall.
