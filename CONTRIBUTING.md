# Contributing

CodeCairn is deliberately small and contract-first. Before changing behavior,
read [`CONTEXT.md`](CONTEXT.md), [`docs/architecture.md`](docs/architecture.md),
and the relevant accepted ADR. GitHub Issues in
`Hackerismydream/CodeCairn` are the work queue; `ready-for-agent` means the
scope is executable.

## Development

Use Python 3.12 and `uv`:

```bash
uv sync --locked --all-groups
make format
make check
make docs-check
```

Changes to evaluation or packaging must also run the relevant Make target,
`make evidence-verify`, `uv build --clear`, and `make artifact-check`.

Keep dependencies pointing inward:

```text
entrypoints -> service -> memory
                 ^          ^
                 |          |
             importers   storage adapters
```

Provider traces and model output are untrusted. Only normalized events may
author provenance, message role, exact quote, command outcome, file change, or
verification facts. Public benchmark numbers require a checked-in manifest
and raw aggregate inputs.

## Change shape

- Keep one pull request focused on one accepted issue or task.
- Add tests through a public CLI, MCP, or service behavior seam.
- Update current documentation and an ADR when a contract changes.
- Do not commit credentials, private traces, runtime roots, downloaded models,
  benchmark datasets, or generated build output.
- Explain observed checks and evidence boundaries; do not present skipped,
  fixture-only, or infrastructure-failed work as verified.

By participating, contributors agree to follow
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
