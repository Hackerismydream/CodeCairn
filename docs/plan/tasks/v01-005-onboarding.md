---
id: v01-005
scope: initialization, configuration, processing, and diagnostics
status: ready
depends-on: [v01-004]
---

# Make a repository usable through `codecairn init`

## Objective

Replace manual root, repository-key, and provider setup with one explicit,
inspectable configuration flow and actionable operations.

## Paths

Primary:

- `src/codecairn/bootstrap.py`
- `src/codecairn/entrypoints/cli.py`
- `src/codecairn/service/application.py`
- `src/codecairn/service/runtime.py`
- `src/codecairn/memory/provider_config.py`
- `pyproject.toml`
- `tests/test_cli.py`
- `tests/test_api.py`
- `tests/test_retrieval_providers.py`
- `README.md`
- `docs/runtime/operations.md`

Add a focused config module under `memory` for value objects and under
`bootstrap`/a boundary module for file and environment loading. Do not make the
domain parse TOML or environment variables.

## Required changes

1. Implement stable Git repository discovery and `repo_key` derivation shared
   by later MCP/hooks.
2. Make `~/.codecairn` the default runtime root; repository-local state requires
   an explicit `--root`.
3. Implement strict, versioned `codecairn.toml` with precedence:
   explicit CLI > environment > file > built-in.
4. Keep secrets environment-only and reject unknown config keys.
5. Implement idempotent `codecairn init` with the flags documented in
   `onboarding-and-operations.md`.
6. Recommend DashScope `qwen3.7-text-embedding` at 1,024 dimensions when its
   key exists. Implement `init --check-provider`/`doctor --live` as an explicit
   one-input embedding smoke; an unchecked profile is configured, not
   live-verified. Otherwise record the documented pinned FastEmbed default.
   Never silently fall back at runtime.
7. Configure semantic capture independently; `none` is a valid visible state.
8. Add `codecairn process` for bounded semantic/index queues and failed retry.
9. Add `memory show`, `memory history`, `memory supersede`, and `memory restore`
   CLI presentation over existing service operations.
10. Make human `doctor` show subsystem state and one executable remedy; retain
    stable JSON and `--strict`.
11. Split the current 2,648-line bootstrap composition so command parsing,
    configuration, and object construction are readable. Do not create a
    generic dependency-injection framework.

## Compatibility

Existing CLI import/index/recall commands remain, but key/root flags become
overrides rather than the normal happy path. Existing HTTP methods, paths,
request-ID/error envelopes, and index behavior remain; memory payloads use the
version 0.1 schema. New lifecycle operations need no HTTP route.

## Verification

```bash
uv run pytest tests/test_cli.py tests/test_api.py tests/test_retrieval_providers.py
uv run codecairn init --help
uv run codecairn process --help
uv run codecairn doctor --help
make format
make check
```

Add an installed-command-style test using a temporary Git repository:

```text
init -> import fixture without repo-key -> process -> recall without repo-key
```

Also cover subdirectory/worktree resolution, no remote, config/env precedence,
unknown keys, no secret serialization, idempotent init, provider absence,
stale index, failed semantic job, and pre-v0.1 root rejection.

Unit tests fake the embedding probe and assert vector shape. Record one optional
credentialed live-smoke command for release; do not claim the live provider
passed from a mock.

## Exit criteria

- the five-minute onboarding path works without hand-written repo keys;
- every degraded doctor row has a tested remedy;
- provider capabilities are independent and explicit;
- bootstrap is materially smaller and has one composition path;
- all checks pass and line deltas are reported.
