# CodeCairn

CodeCairn is a local-first Memory OS for agents. It turns owned Codex and
Claude Code sessions into inspectable long-term memory, keeps human-readable
Markdown as durable truth, and compiles bounded Recall Context for later work.

CodeCairn owns memory; the coding agent owns execution. It is not an agent
runner, IDE, hidden prompt injector, or cloud knowledge platform.

## Status

The repository is pre-release. Local `main@954f728` contains the complete Fable
EverOS-alignment baseline, including public index maintenance, import-time
drain, lazy retrieval providers, and corrected LoCoMo V24 measurement assets.

The version 0.1 product design is accepted but not yet implemented. The
distinction matters:

| Area | Current implementation | Version 0.1 target |
|---|---|---|
| Import | Codex/Claude JSONL to Agent Trace and Task Episode | Same source layer |
| Automatic capture | Deterministic Failed Command only | One Task Experience per Episode plus optional Knowledge |
| Memory model | Six historical types and Evidence Gate service paths | Four types; storage does not require verification |
| Evolution | None | Immutable Supersession, active status, history, restore |
| Recall | Hierarchical indexed recall | Active-only typed recall with pinned Work State |
| Product surfaces | CLI and loopback HTTP | CLI, MCP, and session-end hooks; HTTP compatibility |
| Setup | Manual root, repo key, and provider environment | `codecairn init`, config file, derived repository identity |
| Distribution | Checkout build, no license/tag | MIT, curated PyPI/`uvx` package |
| Source size | 34,091 physical Python lines | at most 10,000 core / 15,000 total |

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
  --root .codecairn

uv run codecairn list \
  --repo-key owner/repository \
  --root .codecairn

uv run codecairn recall "test command failed" \
  --repo-key owner/repository \
  --root .codecairn
```

Import commits Markdown and SQLite first, then drains the index outbox unless
`--no-index` is supplied. A drain failure does not erase durable memory; the
import payload and `doctor` report degraded index state. Operators can use:

```bash
uv run codecairn index status --root .codecairn
uv run codecairn index sync --root .codecairn
uv run codecairn index rebuild --root .codecairn
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
3. Knowledge — Repository Knowledge, User Preference, and Work State.
4. Evolution — immutable Supersession and derived status.
5. Recall — bounded task-shaped context.

It exposes four durable Coding Memory types: Task Experience, Repository
Knowledge, User Preference, and Work State. Debugging, failed commands, and
verified results are Task Experience facets rather than separate top-level
types.

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
