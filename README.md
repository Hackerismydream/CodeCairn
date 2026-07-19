# CodeCairn

CodeCairn is an auditable long-term memory runtime for coding agents. It turns
Codex and Claude Code sessions into evidence-backed Coding Memories, stores
human-readable Markdown as the source of truth, and builds task-shaped Recall
Context for later coding work.

The project is intentionally narrow: it is a memory runtime, not an agent
runner, IDE, or cloud knowledge platform.

## Status

CodeCairn is under active development. No benchmark number is published until
the corresponding dataset, run manifest, verifier output, and aggregation code
are checked into a reproducible report artifact.

The first release is planned in three milestones:

1. Trace, import ledger, Markdown truth, and SQLite state.
2. Evidence-backed extraction, LanceDB indexing, and Recall Context.
3. LoCoMo evaluation, isolated coding-task A/B runs, and recovery evidence.

## Development

CodeCairn requires Python 3.12 and `uv`.

```bash
uv sync --all-groups
make check
```

Project contracts live in [CONTEXT.md](CONTEXT.md),
[docs/architecture.md](docs/architecture.md), and [docs/adr/](docs/adr/).

## License

The repository will receive an explicit open-source license before its first
tagged release. Until then, no license is granted by default.
