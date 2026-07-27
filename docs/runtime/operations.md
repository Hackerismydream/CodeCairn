# Runtime Operations

This document describes behavior implemented on current `main` after
`v01-004`. Target-only lifecycle CLI presentation, installed retrieval
configuration, `init`, MCP, hooks, and release evaluation remain specified under
[`../v0.1/`](../v0.1/).

## Current support matrix

| Capability | CLI | HTTP | Current behavior |
|---|---|---|---|
| Import session | `codecairn import` | `POST /api/v1/import` | Incrementally normalizes Codex or Claude JSONL, closes eligible Task Episodes, and commits one deterministic Task Experience per closed Episode |
| Process semantic work | `codecairn process` | not exposed | Leases bounded semantic jobs when a semantic extractor is configured; the default composition leaves them visibly pending |
| Evolve memory | service interface only | not exposed | Applies validated immutable Supersession, returns deterministic history, and creates forward-only restore revisions |
| List memory | `codecairn list` | `GET /api/v1/memories` | Reads four-type durable memory in one explicit `repo_key` namespace |
| Recall | `codecairn recall` | `POST /api/v1/recall` | Drains a bounded namespace index batch, then compiles active-only hybrid retrieval with optional explicit history |
| Diagnostics | `codecairn doctor` | `GET /api/v1/health` | Reports imports, memories, Write Intent recovery, semantic jobs, and queued index projections |
| Index commands | `codecairn index ...` | index routes | Transitional CLI presentation remains; the lifecycle-aware LanceDB cascade and parity service are implemented |
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
  [--include-superseded] [--workstream-key KEY] [--token-budget N]
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

The current bootstrap does not yet construct production semantic or retrieval
providers. Tests inject explicit test adapters. Provider configuration and the
installed-operation workflow belong to `v01-005`; normal composition returns
`index_not_ready` rather than silently selecting a fallback.

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
Markdown under `evolution/<repo-slug>/` is durable Supersession truth.
SQLite contains:

- import checkpoints and committed-prefix digests;
- first-close-wins Task Episode rows;
- the Source Fact Registry;
- Coding Memory mirrors;
- prepared/completed/conflicted Write Intents;
- pending/leased/completed/failed semantic jobs and immutable output batches;
- Evolution mirrors, proposal outcomes, predecessor claims, and the
  rebuildable active/superseded projection;
- lifecycle-aware index projection jobs with target status, profile identity,
  bounded leases, retries, and failure detail.

Capture and evolution enqueue deterministic parent/Source Fact child
projections. Before recall, a bounded namespace cascade reconciles expected
fingerprints, drains work to the source cursor, and verifies LanceDB parity.
If the cap is exhausted or the configured retrieval identity differs from the
index identity, recall returns `index_not_ready`; it never scans Markdown as a
fallback.

## Diagnostics

`doctor` returns:

- `status`: `ok` unless a Write Intent is conflicted or a semantic job failed;
- import, observed-event, and memory counts;
- pending and conflicted recovery counts;
- semantic job counts by status.

Pending semantic work is expected when no provider is configured and does not
make deterministic capture unhealthy. Failed semantic or index jobs are
visible and bounded. Installed provider selection and actionable remedies land
in onboarding.

## Current boundary

Implemented:

- four durable Coding Memory types;
- incremental Codex/Claude capture;
- stable half-open Episode identity and continuation;
- deterministic bounded Task Experience;
- Write Intent recovery and eight capture fault boundaries;
- retryable semantic proposal batches with source-role enforcement;
- immutable Supersession, lifecycle status, history, and forward-only restore;
- typed source rewrite failure;
- active-only lexical/vector/reranker recall with explicit historical access;
- pinned open Work State selection, per-type caps, total token budget, and an
  attributed sidecar;
- status-aware LanceDB parent/child projections, bounded preflight, rebuild
  parity, and historical evidence verification.

Not yet implemented:

- lifecycle CLI/MCP presentation;
- installed production retrieval configuration;
- `init`, config, namespace derivation, export/reset;
- MCP, Codex/Claude hooks, persistent install, and release evaluation.
