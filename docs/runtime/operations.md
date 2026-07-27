# Runtime Operations

This document describes behavior implemented on current `main` after
`v01-005`. MCP, hooks, release packaging, and release evaluation remain
specified under [`../v0.1/`](../v0.1/).

## Current support matrix

| Capability | CLI | HTTP | Current behavior |
|---|---|---|---|
| Import session | `codecairn import` | `POST /api/v1/import` | Incrementally normalizes Codex or Claude JSONL, closes eligible Task Episodes, and commits one deterministic Task Experience per closed Episode |
| Initialize repository | `codecairn init` | not exposed | Derives and freezes repository identity, writes strict non-secret config, and constructs an explicit retrieval profile |
| Process queued work | `codecairn process` | not exposed | Leases bounded semantic and index jobs; disabled semantic extraction remains visibly pending |
| Direct memory | `codecairn remember` | not exposed | Creates Repository Knowledge, Repository Working Preference, or Work State; direct Task Experience is rejected |
| Evolve memory | `codecairn memory ...` | not exposed | Applies validated immutable Supersession, returns deterministic history, and creates forward-only restore revisions |
| List memory | `codecairn list` | `GET /api/v1/memories` | Reads four-type durable memory in the resolved repository namespace |
| Recall | `codecairn recall` | `POST /api/v1/recall` | Drains a bounded namespace index batch, then compiles active-only hybrid retrieval with optional explicit history |
| Diagnostics | `codecairn doctor` | `GET /api/v1/health` | Reports imports, memories, Write Intent recovery, semantic jobs, and queued index projections |
| Namespace operations | `codecairn namespace ...` | not exposed | Creates a consistent export or performs a confirmation-gated, backup-first reset |
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
codecairn init [--root ROOT] [--repo-key REPO]
  [--retrieval-profile dashscope|fastembed] [--semantic-profile PROFILE]
  [--prefetch] [--check-provider] [--force]
codecairn import SOURCE [--finalize] [--no-index]
codecairn process [--semantic/--no-semantic] [--index/--no-index]
  [--worker-id ID] [--max-jobs N] [--retry-failed]
codecairn list
codecairn recall TASK [--limit N]
  [--include-superseded] [--workstream-key KEY] [--token-budget N]
codecairn remember TYPE [TEXT|--file FILE|--stdin] [type-specific fields]
codecairn memory show MEMORY_ID
codecairn memory history MEMORY_ID
codecairn memory supersede PREDECESSOR_ID SUCCESSOR_ID --reason TEXT
codecairn memory restore MEMORY_ID
codecairn namespace export --output DIR
codecairn namespace reset [--dry-run] --confirm REPO
codecairn doctor [--live] [--strict] [--format human|json]
codecairn index status
codecairn index sync
codecairn index rebuild
codecairn evidence verify BUNDLE_DIR
```

Normal commands discover `<git-common-dir>/codecairn.toml`; `--config`,
`--root`, and `--repo-key` remain explicit automation overrides. The frozen
repo key wins over environment configuration. Linked worktrees share one
binding. Secrets are environment-only and unknown config keys fail startup.

`process` does not invent a provider fallback. With no configured semantic
extractor it reports pending jobs and leaves the deterministic Task Experience
intact. Provider or schema failures are bounded, retryable jobs; completed jobs
reuse their immutable output fingerprint and never call the provider again.

Production composition constructs the recorded retrieval provider. `init`
defaults to the pinned 384-dimension FastEmbed profile, or recommends the
1,024-dimension DashScope profile when `DASHSCOPE_API_KEY` exists. Hashing and
score fusion adapters are test-only. `init --check-provider` and
`doctor --live` perform an explicit live embedding check; an unchecked profile
is only `configured`, never reported as live verified.

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

- `status`: `ok` or `degraded`;
- import, observed-event, and memory counts;
- pending and conflicted recovery counts;
- semantic and index job counts by status;
- one status/remediation row for config, import, semantic, Markdown, SQLite,
  index queue, LanceDB, hooks, and privacy;
- provider verification state and the local/network egress posture.

Pending semantic work is expected when no provider is configured and does not
make deterministic capture unhealthy. Failed semantic or index jobs are
visible and bounded. `--strict` makes degraded state fail automation.

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
- stable Git/common-directory repository binding and namespace derivation;
- explicit pinned FastEmbed and DashScope retrieval composition;
- independent semantic-provider configuration;
- lifecycle/direct-memory/namespace CLI presentation;
- actionable human and stable JSON diagnostics.

Not yet implemented: MCP, Codex/Claude hooks, persistent release installation,
one-command evaluation gates, and release-candidate evidence.
