# CodeCairn

CodeCairn is an auditable local long-term memory runtime for coding agents. It
turns owned Codex and Claude Code sessions into inspectable repository memory,
keeps human-readable Markdown as durable truth, and compiles bounded Recall
Context for later work.

CodeCairn owns memory; the coding agent owns execution. It is not an agent
runner, IDE, hidden prompt injector, or cloud knowledge platform.

## Status

The repository is pre-release. The version 0.1 product design, source-budget
guardrail, historical-evidence boundary, four-type domain, and complete
source-to-memory capture pipeline are implemented. Supersession through release
packaging remains in progress. The distinction between current behavior and the
release target matters:

| Area | Current implementation | Version 0.1 target |
|---|---|---|
| Import | Incremental Codex/Claude trace import with explicit Episode closure, stable continuation, and typed source rewrite failure | Add installed hooks and derived namespace |
| Automatic capture | One deterministic Task Experience per closed Episode; optional semantic work is queued and retryable | Configure production semantic extraction through onboarding |
| Memory model | Four durable types with system-owned provenance | Same model plus lifecycle status |
| Evolution | None | Immutable Supersession, active status, history, restore |
| Recall | Small deterministic lexical compatibility baseline | Active-only hybrid recall with pinned Work State |
| Product surfaces | CLI and loopback HTTP | CLI, MCP, and session-end hooks; HTTP compatibility |
| Setup | Manual root, repo key, and provider environment | `codecairn init`, config file, derived repository identity |
| Distribution | Checkout build, no license/tag | MIT, curated persistent-tool/PyPI package |
| Source size | About 8,200 core / 11,100 total physical Python lines at `v01-002` | at most 10,000 core / 15,000 total |

The implementation plan is
[`docs/plan/README.md`](docs/plan/README.md). Do not treat commands marked as
version 0.1 targets in design documents as available on current `main`.

## Current development path

CodeCairn requires Python 3.12 and `uv`.

```bash
uv sync --all-groups
make check
```

The current CLI requires an explicit runtime root and repository key:

```bash
uv run codecairn import /path/to/session.jsonl \
  --repo-key owner/repository \
  --root .codecairn \
  --finalize

uv run codecairn process \
  --root .codecairn

uv run codecairn list \
  --repo-key owner/repository \
  --root .codecairn

uv run codecairn recall "test command failed" \
  --repo-key owner/repository \
  --root .codecairn
```

Without `--finalize`, manual EOF leaves the final task open; next-user
boundaries still close prior tasks. Stop and SessionEnd callers use the same
service with their explicit boundary kinds.

Import commits through a recoverable Write Intent and enqueues semantic and
index work. The current bootstrap has no implicit semantic-provider fallback,
so `process` reports pending semantic work until `v01-005` configures one.
Current recall is the small SQLite lexical baseline; the queued hybrid index
becomes operational in the active-recall/onboarding tasks. Transitional
diagnostics are available through:

```bash
uv run codecairn index status --root .codecairn
uv run codecairn doctor --root .codecairn
```

The exact current surface is
[`docs/runtime/operations.md`](docs/runtime/operations.md).

## Version 0.1 outcome

The accepted release outcome is:

```text
install
  -> initialize one repository Memory Namespace
  -> connect Codex or Claude Code through MCP and a session-end hook
  -> finish a task
  -> capture Task Experience and optional Knowledge
  -> evolve stale memory through immutable Supersession
  -> recall active, attributed context in the next task
  -> inspect or restore history
```

Version 0.1 has five layers:

1. Source — normalized traces and system-derived observations.
2. Experience — one Task Experience per Task Episode.
3. Knowledge — Repository Knowledge, Repository Working Preference, and Work
   State.
4. Evolution — immutable Supersession and derived status.
5. Recall — bounded task-shaped context.

It exposes four durable Coding Memory types: Task Experience, Repository
Knowledge, User Preference (presented as Repository Working Preference), and
Work State. Debugging, failed commands, and verified results are Task
Experience facets rather than separate top-level types.

Raven integration is intentionally deferred until after version 0.1.

## Evaluation and evidence

The current checked-in evidence bundle is
[`evidence/benchmark-v3`](evidence/benchmark-v3/README.md). Its offline verifier
recomputes the published aggregates and SHA-256 inventory:

```bash
uv run codecairn evidence verify evidence/benchmark-v3
```

The historical bundle reports 82.60% on 1,540 LoCoMo category 1–4 questions.
That result belongs to its frozen commit, architecture, and protocol. It is not
a version 0.1 result until a new release-candidate run is checked in.

Version 0.1 will provide:

```text
make eval-smoke
make eval-scale
make eval-retrieval
make eval-locomo-200
make eval-locomo-full
make eval-coding-ab
make evidence-verify
make source-budget
```

These targets are specified, not yet implemented. Their exact artifact and
release contract is
[`docs/v0.1/evaluation-and-release.md`](docs/v0.1/evaluation-and-release.md).

## Documentation

Start with:

1. [`CONTEXT.md`](CONTEXT.md) — canonical domain language.
2. [`docs/PRD.md`](docs/PRD.md) — accepted version 0.1 product requirements.
3. [`docs/architecture.md`](docs/architecture.md) — target ownership and flows.
4. [`docs/v0.1/walkthrough.md`](docs/v0.1/walkthrough.md) — one trace through
   the system.
5. [`docs/plan/README.md`](docs/plan/README.md) — implementation order and
   agent-ready tasks.
6. [`docs/runtime/operations.md`](docs/runtime/operations.md) — behavior that
   exists on current `main`.

The full maintained index is [`docs/INDEX.md`](docs/INDEX.md).

## Architecture rule

Dependencies point inward:

```text
entrypoints -> service -> memory
                 ^          ^
                 |          |
             importers   storage adapters
```

Markdown is durable truth. SQLite is operational state. LanceDB is a
rebuildable search projection. External model output may propose
interpretations, but only normalized source events may author provenance,
roles, exact quotes, command outcomes, file changes, and verification state.

## License

The accepted version 0.1 license is MIT. The license file and release metadata
are part of task `v01-009`; until that task merges and the first tag is cut, no
license is granted by default.
