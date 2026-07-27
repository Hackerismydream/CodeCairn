# Runtime Operations

This document describes behavior implemented on current `main` after
`v01-002`. Target-only `init`, lifecycle history, MCP, hooks, hybrid retrieval,
and release evaluation remain specified under [`../v0.1/`](../v0.1/).

## Current support matrix

| Capability | CLI | HTTP | Current behavior |
|---|---|---|---|
| Import session | `codecairn import` | `POST /api/v1/import` | Incrementally normalizes Codex or Claude JSONL, closes eligible Task Episodes, and commits one deterministic Task Experience per closed Episode |
| Process semantic work | `codecairn process` | not exposed | Leases bounded semantic jobs when a semantic extractor is configured; the default composition leaves them visibly pending |
| List memory | `codecairn list` | `GET /api/v1/memories` | Reads four-type durable memory in one explicit `repo_key` namespace |
| Recall | `codecairn recall` | `POST /api/v1/recall` | Uses the temporary deterministic lexical baseline over SQLite memory state |
| Diagnostics | `codecairn doctor` | `GET /api/v1/health` | Reports imports, memories, Write Intent recovery, semantic jobs, and queued index projections |
| Index commands | `codecairn index ...` | index routes | Expose transitional queue/status surfaces; hybrid index processing and parity land in `v01-004`/`v01-005` |
| Historical evidence | `codecairn evidence verify` | not exposed | Verifies frozen evidence without loading the live runtime |

## Capture lifecycle

```text
observe source suffix
  -> close next-user spans
  -> close final span only for Stop, SessionEnd, or finalize=true
  -> reserve Episode closure in SQLite
  -> prepare one Write Intent for the complete capture batch
  -> temp-write, file-fsync, atomically create, directory-fsync Markdown
  -> transactionally mirror Episode, Source Facts, Task Experience,
     semantic job, index job, source cursor, and completed intent
```

Manual EOF is not a boundary unless `--finalize` or HTTP `finalize=true` is
explicit. An appended assistant/tool suffix after a committed boundary becomes
a linked continuation Episode. A new user event starts an independent Episode.
Repeated boundaries at an unchanged cursor are no-ops.

The Write Intent protocol recovers missing deterministic files before every
mutating operation. Conflicting immutable bytes mark the intent `conflicted`;
they are never silently overwritten or deleted. A normalized committed-prefix
change returns the typed `source_rewritten` error without advancing the source
cursor.

## CLI

```text
codecairn import SOURCE --repo-key REPO --root ROOT [--finalize] [--no-index]
codecairn process --root ROOT [--worker-id ID] [--max-jobs N]
codecairn list --repo-key REPO --root ROOT
codecairn recall TASK --repo-key REPO --root ROOT [--limit N]
codecairn doctor --root ROOT
codecairn index status --root ROOT
codecairn index sync --root ROOT
codecairn index rebuild --root ROOT
codecairn evidence verify BUNDLE_DIR
```

`process` does not invent a provider fallback. With no configured semantic
extractor it reports pending jobs and leaves the deterministic Task Experience
intact. Provider or schema failures are bounded, retryable jobs; completed jobs
reuse their immutable output fingerprint and never call the provider again.

The current bootstrap does not yet construct a production semantic extractor.
Tests inject the port directly. Provider configuration and installed-operation
workflow belong to `v01-005`.

## HTTP

The compatibility import request is:

```json
{
  "source_path": "/allowed/root/session.jsonl",
  "repo_key": "owner/repository",
  "finalize": false,
  "index": true
}
```

HTTP source paths must remain beneath a configured source root. The server
binds only to loopback. `SourceRewritten` is returned as
`error.code="source_rewritten"`; generic malformed traces use
`error.code="trace_invalid"`.

CLI and HTTP call the same `CodeCairnApplication` facade. HTTP does not create
a watcher, queue worker, semantic provider, or background index worker.

## Durable state and queues

Markdown under `memory/<repo-slug>/<memory-type>/` is durable memory truth.
SQLite contains:

- import checkpoints and committed-prefix digests;
- first-close-wins Task Episode rows;
- the Source Fact Registry;
- Coding Memory mirrors;
- prepared/completed/conflicted Write Intents;
- pending/leased/completed/failed semantic jobs and immutable output batches;
- pending index projection jobs.

Capture enqueues index work but the current recall baseline reads committed
SQLite memory directly. An import response therefore reports `index.synced`
false while projection jobs remain pending. Do not describe the current
transitional index commands as LanceDB parity or retrieval readiness.

## Diagnostics

`doctor` returns:

- `status`: `ok` unless a Write Intent is conflicted or a semantic job failed;
- import, observed-event, and memory counts;
- pending and conflicted recovery counts;
- semantic job counts by status.

Pending semantic work is expected when no provider is configured and does not
make deterministic capture unhealthy. A failed semantic job is visible and
retryable. Pending index projections remain a known transitional state until
the active-recall/onboarding tasks implement the production index lifecycle.

## Current boundary

Implemented:

- four durable Coding Memory types;
- incremental Codex/Claude capture;
- stable half-open Episode identity and continuation;
- deterministic bounded Task Experience;
- Write Intent recovery and eight capture fault boundaries;
- retryable semantic proposal batches with source-role enforcement;
- typed source rewrite failure;
- lexical recall compatibility and historical evidence verification.

Not yet implemented:

- Supersession, active/superseded projection, history, and restore;
- production hybrid retrieval, reranking, token-budget compilation, and index
  draining;
- `init`, config, namespace derivation, export/reset;
- MCP, Codex/Claude hooks, persistent install, and release evaluation.
