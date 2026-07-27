# Version 0.1 Agent Integration

## Purpose

This document specifies how Codex, Claude Code, and humans use the same
CodeCairn lifecycle. It defines presentation contracts only; all behavior is
owned by service use cases.

## Shared service operations

The CLI, MCP server, and hook adapter compose one application facade with these
operations:

```text
initialize
import_session
remember
process_pending
recall
list_memories
get_memory
memory_history
supersede
restore
index_status / index_sync / index_rebuild
doctor
export_namespace
reset_namespace
```

Entrypoints translate transport values and errors. They do not select a
different memory type, supersession policy, or fallback provider.

## CLI contract

The target command tree is:

```text
codecairn init
codecairn import <source> [--finalize]
codecairn remember <repository-knowledge|user-preference|work-state>
  (--content TEXT | --file PATH | --stdin)
  --title TEXT
  [--category CATEGORY] [--tag TAG]...
  [--subject-key KEY | --workstream-key KEY]
  [--workstream-state open|closed]
  [--source-fact-id ID]...
codecairn process
codecairn recall <task> [--workstream-key KEY]
codecairn list
codecairn memory show <memory-id>
codecairn memory history <memory-id>
codecairn memory supersede <old-id> <new-id>
codecairn memory restore <memory-id>
codecairn index status|sync|rebuild
codecairn hook install|run
codecairn doctor
codecairn namespace export --output PATH
codecairn namespace reset --dry-run
codecairn namespace reset --confirm <repo-key>
codecairn eval ...
codecairn evidence verify ...
```

Commands default to the current repository namespace recorded by `init`.
`--repo-key` and `--root` remain explicit overrides for automation.

Mutating commands emit the created memory or Evolution Record ID. Structured
automation uses `--format json`; human output uses compact text. Errors use a
stable code, one-line explanation, and one remediation when one exists.

There is no in-place `memory edit` or permanent per-item delete in version 0.1.
Namespace export writes a manifest plus authoritative Markdown and operational
backup. Reset first shows the exact Markdown, SQLite, and LanceDB targets,
requires the resolved repo key as confirmation, and moves them to a
timestamped recoverable backup rather than unlinking them in place.

`remember` accepts exactly one content source. Repository Knowledge requires
`subject_key`; Work State requires `workstream_key` and state fields; User
Preference requires one or more `source_fact_id` values that resolve to
user-authored registry facts. Task Experience is rejected because it is
Episode-derived. The full DTO and bounds are defined by
[`schema-contract.md`](schema-contract.md).

## MCP server

The package exposes a dedicated stdio program:

```text
codecairn-mcp
```

Protocol stdout contains MCP frames only. Diagnostics go to stderr or the
runtime diagnostic store. The server resolves the repository namespace from
its working directory unless a tool argument overrides it.

### Tools

| Tool | Required input | Optional input | Result |
|---|---|---|---|
| `recall` | `task` | `repo_key`, `workstream_key`, `limit`, `include_superseded` | Recall Markdown plus structured sidecar |
| `remember` | `memory_type`, `title`, `content` | `repo_key`, `subject_key`, `workstream_key`, `workstream_state`, `tags`, `source_fact_ids` | Created memory and any applied evolution |
| `list_memories` | none | `repo_key`, `memory_type`, `status`, `limit`, `cursor` | Compact page of memories |
| `get_memory` | `memory_id` | `repo_key` | Full durable memory and resource URI |
| `memory_history` | `memory_id` | `repo_key` | Ordered predecessor/successor chain |
| `import_session` | `source_path` | `repo_key` | Import result and pending job counts |
| `doctor` | none | `repo_key` | Structured subsystem health and remedies |

Rules:

- `remember` rejects Task Experience because that type is episode-derived.
- `remember` may create Repository Knowledge or Work State with
  `origin=agent_asserted`.
- `remember` may create User Preference only when `source_fact_ids` resolve to
  normalized user-authored events; arbitrary raw references are not accepted.
- Repository Knowledge and User Preference require `subject_key`; Work State
  requires `workstream_key` and `workstream_state`.
- `include_superseded` defaults to `false`.
- tool errors never include secrets, raw stack traces, or fake empty success.
- `source_path` must pass the same owned-root validation as the CLI.
- every string, collection, page, recall, and context limit comes from the
  schema contract; transports do not pick their own caps.

The package pins the stable MCP Python SDK line `mcp>=1.27,<2` for version 0.1.
Tool input/output schemas and the resource template are checked-in JSON
snapshots generated from service DTOs. Pagination cursors use the opaque
canonical encoding in the schema contract. Cancellation may release an
operational lease but never rolls back a committed durable write.

MCP errors contain `code`, `message`, optional `remediation`, and `retryable`.
They never return fake empty success. `context_too_large`,
`cursor_invalid`, `foreign_namespace`, `index_not_ready`,
`semantic_not_configured`, `source_unavailable`, and `schema_invalid` are
stable public error codes where applicable.

### Resource

```text
codecairn://memory/{memory_id}
```

The resource returns the canonical Markdown for one Coding Memory. Unknown,
foreign-namespace, malformed, or unsafe IDs return typed protocol errors.
Evolution history remains a tool response rather than an unbounded resource
tree in version 0.1.

### Registration

After package installation:

```bash
claude mcp add codecairn -- codecairn-mcp
codex mcp add codecairn -- codecairn-mcp
```

The onboarding command prints these commands. It does not silently rewrite
client MCP configuration.

## Hook adapter

Hooks perform post-session capture; they do not inject recall into a prompt.

```text
client event JSON on stdin
  -> client-specific envelope adapter
  -> validate session ID, transcript/source path, and cwd
  -> resolve initialized Memory Namespace
  -> import owned trace transactionally
  -> enqueue semantic and index work
  -> record success/failure receipt
  -> exit 0 with empty stdout
```

### Supported events

| Client | Event | Interpretation |
|---|---|---|
| Claude Code | `SessionEnd` | Import the completed transcript named by `transcript_path` |
| Codex | `Stop` | Close/import the current turn Episode; duplicate firing with no new event is harmless |

The adapters are pinned to checked-in event fixtures. Client schema drift must
produce an actionable hook failure, never guessed provenance.

### Supported-client matrix

| Client | Minimum tested version | Event | Transcript/source contract | Installed timeout | Trust |
|---|---:|---|---|---:|---|
| Codex CLI | `0.144.6` | `Stop` | `transcript_path` may be null; session-ID fallback is permitted only for a checked-in, versioned local-layout resolver | 5 seconds | Project hooks run only after explicit Codex review/trust |
| Claude Code | `2.1.220` | `SessionEnd` | `transcript_path` must resolve to an owned readable JSONL source | 5 seconds | Installer changes only the explicitly selected settings scope |

These are minimum tested versions, not claims about older clients. Release
fixtures record exact client version and source. The transcript formats are
adapter inputs rather than stable upstream protocols, so unsupported shapes
produce `unsupported_client` or `source_unavailable` receipts and fall back to
the printed manual import command.

Hook startup must not compose retrieval or semantic providers. The offline
cold-start P95 is at most one second for a no-op receipt and at most four
seconds for the release fixture import. Client config uses an explicit
five-second timeout. Codex command hooks run from session cwd and are not
treated as asynchronous; installed commands therefore use the persistent
absolute executable rather than repository-relative Python.

### Command

```text
codecairn hook run --client claude
codecairn hook run --client codex
```

The command:

- reads exactly one JSON object from stdin;
- writes nothing to stdout;
- never blocks or changes the client decision;
- exits zero after recording malformed input, source, schema, or storage
  failure;
- synchronously commits deterministic Task Experience, its index outbox, and
  the Hook Receipt;
- leaves model extraction and full projection draining to
  `codecairn process`; recall may perform only its bounded deterministic
  preflight;
- skips sessions whose working directory is inside the CodeCairn runtime root;
- redacts secrets and bounds diagnostic payloads.

The client hook timeout is configured explicitly by the installer. Import
idempotency makes retries safe. A hook does not call a model provider or drain
the full index.

### Installation

```text
codecairn hook install --claude
codecairn hook install --codex
codecairn hook install --claude --codex --dry-run
```

Installation is a deliberate external configuration change:

1. `--dry-run` prints the exact merge patch and target path.
2. Normal mode parses the existing JSON, preserves unrelated keys and hooks,
   adds one stable CodeCairn handler, writes atomically, and reads back.
3. Re-running is an idempotent no-op.
4. Invalid or unsupported client configuration stops without writing.
5. The command prints an exact uninstall/removal instruction.
6. A successful install immediately runs non-mutating hook diagnostics.

Claude Code targets the supported settings scope selected by the user. Codex
targets `.codex/hooks.json` for the selected scope. Implementation must verify
the installed client schema rather than assume a stale example.

## Processing ownership

Hooks intentionally leave expensive work queued. Every `recall` must first
perform a bounded current-namespace drain of deterministic index jobs up to the
Hook Receipt/source cursor required by that namespace. The preflight has
configured job and time caps. If it cannot reach readiness, recall returns
`index_not_ready` with a remediation command and does not search an older
projection.

Semantic work may remain pending or failed; deterministic Task Experience is
still recallable and the Recall sidecar exposes `source_cursor`,
`index_cursor`, `semantic_state`, and `freshness`.

The explicit full-drain command remains:

```text
codecairn process [--semantic] [--index] [--retry-failed]
```

`doctor` reports:

- last hook success and failure;
- unprocessed source receipts;
- pending/failed semantic jobs;
- pending/failed index jobs;
- exact retry or repair command.
- current privacy posture:
  `storage=local`, `embedding=local|network`,
  `semantic=disabled|network`, and
  `source_content_egress=none|memory_text|trace_excerpts`.

Human CLI recall prints one short stderr warning when an unacknowledged recent
Hook Receipt failed; the structured Recall result remains valid and carries
the diagnostic reference. MCP keeps protocol output clean and reports the same
reference in its sidecar.

The release does not require a watcher daemon or background service.

## Acceptance tests

1. An in-process MCP client calls all seven tools and fetches the resource.
2. MCP stdout remains protocol-clean when a provider is missing or a tool
   fails.
3. A synthetic Claude SessionEnd fixture imports exactly once across two runs.
4. A synthetic Codex Stop fixture imports appended events without duplicating
   a Task Experience.
5. Malformed hook JSON exits zero, writes no stdout, and appears in `doctor`.
6. Hook installation preserves unrelated configuration and is idempotent.
7. Client-version fixtures document the supported schema versions.
8. CLI and MCP results share IDs, statuses, and error codes for the same use
   case.
9. Hook fixture, no explicit `process`, then recall returns the new Task
   Experience or a typed `index_not_ready`, never stale success.
10. Each client fixture is replayed 100 times without duplicate Episode or
    memory creation.
11. Nullable Codex transcript, unsupported version, untrusted project hook,
    timeout, and cold start are explicit acceptance cases.

## Deferred

- Raven adapter;
- prompt-time automatic injection;
- SessionStart recall hook;
- watcher daemon;
- HTTP parity for evolution operations;
- remote MCP transport.
