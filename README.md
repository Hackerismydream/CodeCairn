# CodeCairn

CodeCairn is a local, auditable long-term memory runtime for coding agents. It
turns owned Codex and Claude Code sessions into inspectable repository memory,
keeps Markdown as durable truth, evolves stale knowledge without deleting
history, and returns bounded attributed context to later sessions.

CodeCairn owns memory. Codex and Claude Code remain independent clients that
own planning, tool use, and code changes. CodeCairn is not an agent runner,
cloud knowledge service, hidden prompt injector, or IDE.

## Status

Version 0.1 is a release candidate, not a published package. The current
implementation includes incremental Codex/Claude import, four memory types,
immutable supersession and restore, active-only hybrid recall, CLI, seven MCP
tools plus one resource, explicit session-end hooks, and reproducible
evaluation gates. Final real-client and candidate-bound paid evidence remain
release blockers.

The built release-candidate wheel can be installed persistently today. The registry command
below becomes the normal path only after the first publication:

```bash
uv tool install codecairn==0.1.0
```

For a local release artifact, replace the package name with the downloaded
wheel path:

```bash
uv tool install ./codecairn-0.1.0-py3-none-any.whl
```

CodeCairn requires Python 3.12. `uv tool install` creates an isolated persistent
environment and puts `codecairn` and `codecairn-mcp` on `PATH`.

## Five-minute memory loop

Run these commands inside the Git repository whose memory CodeCairn should
own:

```bash
codecairn init
codecairn import /path/to/owned-codex-or-claude-session.jsonl --finalize
codecairn recall "What happened in the last repository task?" --format markdown
codecairn doctor
```

`init` writes a strict non-secret binding into the Git common directory and
defaults runtime data to `~/.codecairn`. It chooses the pinned local FastEmbed
retrieval profile unless DashScope is explicitly configured. Semantic
extraction is independent and defaults visibly to `none`; deterministic Task
Experience capture still works without it.

Manual EOF does not close the final task unless `--finalize` is present. A
Codex Stop or Claude Code SessionEnd hook supplies its explicit boundary.
Repeated imports and repeated hooks are idempotent.

## Connect one coding agent

Register the explicit stdio MCP server:

```bash
claude mcp add codecairn -- codecairn-mcp
# or
codex mcp add codecairn -- codecairn-mcp
```

Preview the exact hook edit before installing it:

```bash
codecairn hook install --claude --dry-run
codecairn hook install --claude

# or
codecairn hook install --codex --dry-run
codecairn hook install --codex
```

The installer preserves unrelated settings, writes atomically, and reports the
exact handler command. For Codex, reopen the repository and complete Codex's
normal trust review after inspecting the generated hook configuration;
CodeCairn does not bypass or modify client trust.

Then:

```text
doctor -> complete one coding task -> let the hook capture it
       -> start the next task -> call recall through MCP
```

The complete timed install, MCP, hook, trust, doctor, task, and next-recall
checklist is in
[`docs/runtime/installation.md`](docs/runtime/installation.md). Reviewable
`AGENTS.md` and `CLAUDE.md` snippets are in
[`docs/runtime/agent-instructions.md`](docs/runtime/agent-instructions.md);
CodeCairn never edits those files automatically.

## What is stored

Version 0.1 has five layers but only four durable Coding Memory types:

| Layer | Responsibility | Durable memory types |
|---|---|---|
| Source | Normalize owned traces and derive provenance facts | none |
| Experience | Bound one user task and observed outcome | Task Experience |
| Knowledge | Keep reusable repository facts and current work | Repository Knowledge, Repository Working Preference, Work State |
| Evolution | Append supersession decisions and derive active status | none |
| Recall | Compile bounded task-shaped context and a sidecar | none |

Source and Recall are boundaries, not extra memory types. Evolution records
relationships between immutable revisions. A restored historical memory is a
new forward revision; CodeCairn never rewrites the old memory or reverses
history.

Markdown under the runtime root is durable truth. SQLite owns operational
cursors, mirrors, recovery intents, lifecycle projection, and queues. LanceDB
is a disposable lexical/vector search projection. A model may summarize or
propose knowledge, but it may not author provenance, message roles, exact
quotes, command outcomes, changed files, or verification facts.

## Public surfaces

The CLI supports setup, import, direct Knowledge/Work State, recall, history,
restore, queues, index operations, doctor, export/reset, hooks, and evidence
verification. The stdio MCP server exposes exactly:

```text
recall
remember
list_memories
get_memory
memory_history
import_session
doctor

codecairn://memory/{memory_id}
```

CLI, MCP, and hooks call the same application service. There is no version 0.1
HTTP server. Exact commands and failure behavior are documented in
[`docs/runtime/operations.md`](docs/runtime/operations.md).

## Evaluation and evidence

The authoritative repository gates are:

```bash
make eval-smoke
make eval-scale
make eval-retrieval
make eval-locomo-200 HELP=1
make eval-locomo-full HELP=1
make eval-coding-ab HELP=1
make evidence-verify
make source-budget
```

The offline retrieval protocol currently executes 100 queries against the
public recall path. The scale gate imports 1,000 sessions and 100,000 events
twice. The HELP plans resolve paid inputs, immutable output paths, credentials,
and spend boundaries without calling a provider.

The current [`evidence/v0.1-rc1`](evidence/v0.1-rc1/metrics.json) bundle binds
the version 0.1 implementation to 1,264/1,540 LoCoMo answers (82.08%), a
memory-off 80% to memory-on 100% CodingMemoryBench result across 120 isolated
Codex runs, and the offline, packaging, and real-client gates described below.
The preferred 85% to 86% LoCoMo ship band was not reached. Historical bundles
remain under `evidence/benchmark-v*` and are not presented as current
candidate evidence.

Packaging and documentation gates are:

```bash
uv build --clear
make artifact-check
make docs-check
make installed-smoke
make artifact-repro
```

`artifact-repro` builds twice from separate clean checkouts with a frozen
source epoch and compares both archive hashes and unpacked inventories.

## Learn the architecture

Read from product behavior inward:

1. [`CONTEXT.md`](CONTEXT.md) — canonical vocabulary.
2. [`docs/v0.1/walkthrough.md`](docs/v0.1/walkthrough.md) — one trace through
   capture, evolution, recall, and history.
3. [`docs/architecture.md`](docs/architecture.md) — ownership and dependency
   direction.
4. [`docs/v0.1/learning-path.md`](docs/v0.1/learning-path.md) — guided
   source-reading order.
5. [`docs/runtime/operations.md`](docs/runtime/operations.md) — commands that
   exist on current `main`.
6. [`docs/adr/README.md`](docs/adr/README.md) — why the design changed.

The full maintained index is [`docs/INDEX.md`](docs/INDEX.md).

Dependencies point inward:

```text
entrypoints -> service -> memory
                 ^          ^
                 |          |
             importers   storage adapters
```

## Development

```bash
uv sync --locked --all-groups
make format
make check
make docs-check
```

Contribution and security boundaries are in
[`CONTRIBUTING.md`](CONTRIBUTING.md) and [`SECURITY.md`](SECURITY.md).

## Scope

Version 0.1 deliberately excludes Raven integration, UI/dashboard, cloud
tenancy, background watcher, remote MCP transport, dynamic profiles, and
standalone memory verification. Those are later product decisions, not hidden
release work.

## License

CodeCairn is available under the [MIT License](LICENSE).
