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
```

Entrypoints translate transport values and errors. They do not select a
different memory type, supersession policy, or fallback provider.

## CLI contract

The target command tree is:

```text
codecairn init
codecairn import <source>
codecairn remember
codecairn process
codecairn recall <task>
codecairn list
codecairn memory show <memory-id>
codecairn memory history <memory-id>
codecairn memory supersede <old-id> <new-id>
codecairn memory restore <memory-id>
codecairn index status|sync|rebuild
codecairn hook install|run
codecairn doctor
codecairn eval ...
codecairn evidence verify ...
```

Commands default to the current repository namespace recorded by `init`.
`--repo-key` and `--root` remain explicit overrides for automation.

Mutating commands emit the created memory or Evolution Record ID. Structured
automation uses `--format json`; human output uses compact text. Errors use a
stable code, one-line explanation, and one remediation when one exists.

There is no in-place `memory edit` or permanent per-item delete in version 0.1.

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
| `recall` | `task` | `repo_key`, `limit`, `include_superseded` | Recall Markdown plus structured sidecar |
| `remember` | `memory_type`, `title`, `content` | `repo_key`, `subject_key`, `workstream_key`, `workstream_state`, `tags`, `source_refs` | Created memory and any applied evolution |
| `list_memories` | none | `repo_key`, `memory_type`, `status`, `limit`, `cursor` | Compact page of memories |
| `get_memory` | `memory_id` | `repo_key` | Full durable memory and resource URI |
| `memory_history` | `memory_id` | `repo_key` | Ordered predecessor/successor chain |
| `import_session` | `source_path` | `repo_key` | Import result and pending job counts |
| `doctor` | none | `repo_key` | Structured subsystem health and remedies |

Rules:

- `remember` rejects Task Experience because that type is episode-derived.
- `remember` may create Repository Knowledge or Work State with
  `origin=agent_asserted`.
- `remember` may create User Preference only when `source_refs` resolve to
  normalized user-authored events.
- Repository Knowledge and User Preference require `subject_key`; Work State
  requires `workstream_key` and `workstream_state`.
- `include_superseded` defaults to `false`.
- tool errors never include secrets, raw stack traces, or fake empty success.
- `source_path` must pass the same owned-root validation as the CLI.

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

### Command

```text
codecairn hook run --client claude
codecairn hook run --client codex
```

The command:

- reads exactly one JSON object from stdin;
- writes nothing to stdout;
- never blocks or changes the client decision;
- exits zero after recording malformed input, provider failure, or storage
  failure;
- does only source import and queue creation synchronously;
- leaves model extraction and projection draining to `codecairn process`;
- skips sessions whose working directory is inside the CodeCairn runtime root;
- redacts secrets and bounds diagnostic payloads.

The client hook timeout is configured explicitly by the installer. Import
idempotency makes retries safe.

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

Claude Code targets the supported settings scope selected by the user. Codex
targets `.codex/hooks.json` for the selected scope. Implementation must verify
the installed client schema rather than assume a stale example.

## Processing ownership

Hooks intentionally leave expensive work queued. Any foreground product command
may perform a bounded drain, and the explicit command is:

```text
codecairn process [--semantic] [--index] [--retry-failed]
```

`doctor` reports:

- last hook success and failure;
- unprocessed source receipts;
- pending/failed semantic jobs;
- pending/failed index jobs;
- exact retry or repair command.

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

## Deferred

- Raven adapter;
- prompt-time automatic injection;
- SessionStart recall hook;
- watcher daemon;
- HTTP parity for evolution operations;
- remote MCP transport.
