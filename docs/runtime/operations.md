# Runtime Operations

This document describes behavior implemented on current `main`, including the
`v02-001` Pico trace importer and `v02-002` installed Pico Memory Backend
adapter. Pico default selection, EverOS removal, continuity evidence, and paid
paired evaluation remain downstream work and are not claimed here.

## Current support matrix

| Capability | Command | Current behavior |
|---|---|---|
| Import session | `codecairn import` | Incrementally normalizes Codex, Claude, or CodeCairn-owned Pico JSONL, closes eligible Task Episodes, and commits one deterministic Task Experience per closed Episode |
| Initialize repository | `codecairn init` | Derives and freezes repository identity, writes strict non-secret config, and constructs an explicit retrieval profile |
| Process queued work | `codecairn process` | Leases bounded semantic and index jobs; disabled semantic extraction remains visibly pending |
| Direct memory | `codecairn remember` | Creates Repository Knowledge, Repository Working Preference, or Work State; direct Task Experience is rejected |
| Session hooks | `codecairn hook install/run` | Claude `SessionEnd` and Codex `Stop` import owned transcripts without model calls or client blocking |
| Evolve memory | `codecairn memory ...` | Applies validated immutable Supersession, returns deterministic history, and creates forward-only restore revisions |
| List memory | `codecairn list` | Reads four-type durable memory in the resolved repository namespace |
| Recall | `codecairn recall` | Drains a bounded namespace index batch, then compiles active-only hybrid retrieval with optional explicit history |
| Diagnostics | `codecairn doctor` | Reports imports, memories, Write Intent recovery, semantic jobs, and queued index projections |
| Namespace operations | `codecairn namespace ...` | Creates a consistent export or performs a confirmation-gated, backup-first reset |
| Index commands | `codecairn index ...` | Operates the lifecycle-aware LanceDB cascade and parity service |
| Historical evidence | `codecairn evidence verify` | Verifies frozen evidence without loading the live runtime |
| Distribution | `uv tool install` | Installs curated MIT-licensed wheel entrypoints into an isolated persistent tool environment |
| Pico plugin | `memory.backend = "codecairn"` | Installed discovery exposes one repository-bound MemoryBackend; Pico must consume the final immutable handoff before selecting it by default |

## Capture lifecycle

```text
observe source suffix
  -> close next-user spans
  -> close final span only for Stop, SessionEnd, or --finalize
  -> reserve Episode closure in SQLite
  -> prepare one Write Intent for the complete capture batch
  -> temp-write, file-fsync, atomically create, directory-fsync Markdown
  -> transactionally mirror Episode, Source Facts, Task Experience,
     semantic job, index job, source cursor, and completed intent
```

Manual EOF is not a boundary unless `--finalize` is explicit. An appended
assistant/tool suffix after a committed boundary becomes
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
codecairn hook install [--claude] [--codex] [--dry-run]
codecairn hook run --client claude|codex
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

## MCP

`codecairn-mcp` is a protocol-clean stdio program over the same
`CodeCairnApplication` used by CLI:

| Tool | Behavior |
|---|---|
| `recall` | Returns Recall Markdown and the complete structured sidecar |
| `remember` | Creates direct Knowledge, user-sourced Working Preference, or Work State |
| `list_memories` | Returns a bounded page with an opaque namespace-bound cursor |
| `get_memory` | Returns one full memory, lifecycle status, and resource URI |
| `memory_history` | Returns one ordered immutable lineage |
| `import_session` | Imports one owned source through a manual-finalize boundary |
| `doctor` | Returns structured subsystem health and remedies |

The one resource template is `codecairn://memory/{memory_id}` and returns the
canonical durable Markdown. Typed IDs are validated before storage lookup;
unsafe, missing, and foreign-namespace reads are errors.

Tool inputs are closed and bounded. Direct Task Experience and source-less User
Preference are rejected. MCP errors expose a bounded JSON envelope with
`code`, `message`, `remediation`, and `retryable`; they contain no stack trace
or provider secret. Checked-in input/output/resource schemas live in
[`../schemas/mcp-v01.json`](../schemas/mcp-v01.json).

Registration is explicit and never edits client settings automatically:

```bash
claude mcp add codecairn -- codecairn-mcp
codex mcp add codecairn -- codecairn-mcp
```

## Hooks

`codecairn hook install --claude|--codex [--dry-run]` validates the installed
client version, merges one absolute five-second command into the selected JSON
settings, preserves unrelated entries and file mode, writes atomically, and
verifies readback. A second install is byte-identical. The emitted `uninstall`
field identifies the exact handler command to remove; removal is an explicit
manual settings edit in v0.1.

`codecairn hook run --client claude|codex` reads one bounded stdin JSON value,
normalizes a Claude Code `SessionEnd` or Codex `Stop`, resolves the initialized
repository namespace, and imports the owned transcript with the corresponding
closure boundary. Codex may use its session ID only when exactly one source
matches the supported local session layout. The hook does not compose semantic
or retrieval providers and does not drain the full index.

Every invocation exits zero with empty stdout. Success, no-op, unsupported,
and failure outcomes are recorded as bounded Hook Receipts; failures degrade
`doctor` and include `codecairn import <owned-session.jsonl>` as the manual
fallback. Repeated events reuse the source cursor, and an appended Stop imports
only the new Episode. A cwd inside the runtime root is skipped.

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

Capture and evolution enqueue deterministic parents and exact children.
Capture-derived memory uses Source Fact children; direct multiline memory
without facts uses up to 128 exact source-line children. Recall reranks at most
12 children per parent and globally packs the best excerpts under the token
budget. Before recall, a bounded namespace cascade reconciles expected
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
- incremental Codex/Claude capture plus the CodeCairn-owned Pico Source Journal
  and provider `pico` importer;
- stable half-open Episode identity and continuation;
- deterministic bounded Task Experience;
- Write Intent recovery and eight capture fault boundaries;
- retryable semantic proposal batches with source-role enforcement;
- immutable Supersession, lifecycle status, history, and forward-only restore;
- typed source rewrite failure;
- active-only parent/child lexical/vector/reranker recall with explicit
  historical access;
- pinned open Work State selection, a 20-parent Repository Knowledge cap,
  globally packed exact excerpts, a strict total token budget, and an
  attributed sidecar;
- status-aware LanceDB parent/child projections, bounded preflight, rebuild
  parity, and historical evidence verification.
- stable Git/common-directory repository binding and namespace derivation;
- explicit pinned FastEmbed and DashScope retrieval composition;
- independent semantic-provider configuration;
- lifecycle/direct-memory/namespace CLI presentation;
- actionable human and stable JSON diagnostics;
- seven explicit MCP tools, one canonical Markdown resource, bounded opaque
  pagination, and protocol-clean packaged stdio.
- Claude `SessionEnd` and Codex `Stop` import hooks, atomic/idempotent settings
  installation, bounded receipts, and hook-to-recall read-your-writes.
- installed Pico plugin discovery, workspace-bound fail-closed startup,
  after-Turn journal/import/index readiness, compiled-context user recall,
  empty Agent-track recall, and no-op feedback.

## Pico adapter

The wheel registers entry point `codecairn` in `pico.plugins`. Its resource
package contains manifest `codecairn-memory`, which contributes exactly one
Memory Backend named `codecairn` and no tool or media capability.

Pico startup uses `PluginContext.services.workspace` to resolve the target Git
repository and requires a prior `codecairn init` with a runtime root outside
that repository. It validates provider/index health, recovers staged Pico
journals, and fails closed with stable remediation. Recall maps only Pico's
user track to repository recall and returns one compiled context with
system-derived sidecar metadata and score `0.0`; the Agent track returns `[]`.
Store appends and imports one durable `pico_turn_end` batch, then requires index
readiness before returning. All blocking CodeCairn calls are offloaded from
Pico's event loop.

The adapter does not own Local Skills, add a media tool, fall back to EverOS,
or initialize repositories implicitly.

Not yet implemented: Pico default selection and EverOS removal, joint Pico
continuity/effect evidence, real-client release smoke, publication/tagging,
and candidate-bound release evidence.
