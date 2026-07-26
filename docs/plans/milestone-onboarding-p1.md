# Milestone: Onboarding P1 — from eight exports to two commands

Parent plan: `everos-alignment-roadmap.md` (product program, step P1).
Depends on: usability P0 (index surface, lazy providers) — merged on this branch.

## Problem

Setup today requires cloning, `uv sync`, up to eight environment exports, hand-
locating a session JSONL, and inventing a repo key. Verified friction from the
2026-07-26 product audit: 28 distinct `CODECAIRN_*` env vars and no config
file (F15); `--repo-key` mandatory, manual, and typo-unsafe while the raw
traces already carry `cwd` and git metadata (F4); `--root` defaults cwd-
relative so running from a sibling directory silently queries an empty store
(F6); no way to list repos or inspect one memory (F5); first recall blocks on
a silent ~90 MB model download (F11); `doctor` prints a JSON blob with no
remediation and exit code 0 even when degraded (F13).

## Design

1. **`codecairn init`** — idempotent setup, non-interactive by default, flags
   over prompts (`--root`, `--repo-key`, `--profile`, `--prefetch/--no-prefetch`,
   `--force`):
   - derives the repo key from `git remote get-url origin` (owner/repo),
     falling back to a path slug; prints the derivation,
   - picks the retrieval profile as an **explicit recorded choice**: `dashscope`
     when a key is present, otherwise offers `fastembed` — never a silent
     fallback (ADR 0013/0015 stance),
   - writes a commented `codecairn.toml` into the runtime root,
   - optionally prefetches the reranker artifact,
   - ends by printing a working `import` + `recall` command pair.
2. **Config file** — `codecairn.toml` in the runtime root, loaded by
   bootstrap; precedence `env > codecairn.toml > built-in defaults` (env must
   win for CI and secrets). Keys mirror the existing env vars; unknown keys
   are a startup error, not a warning.
3. **Human doctor** — `doctor --format text` (default on TTY; `json` remains
   the default for pipes and is byte-stable for `/api/v1/health` parity): one
   line per subsystem plus a remediation command per failure
   ("index is 1 revision behind -> run `codecairn index sync`"). `--strict`
   exits non-zero when degraded; default exit stays 0 for script
   compatibility.
4. **Discovery commands** — `codecairn repos` (repo namespaces with memory
   counts, from the existing cross-repo SQLite listing), `codecairn memory
   show <memory_id>` (Markdown truth plus evidence refs), `codecairn
   prefetch` (warm the model cache with progress output).
5. **Repo-key derivation helper** lives in `service/` (single implementation,
   reused later by the MCP server and hooks).
6. **README quickstart rewrite** — the two-command path (`uvx`-style install
   note deferred until packaging; from checkout: `codecairn init` then
   `codecairn import`).

## Acceptance criteria

1. In a git checkout with no CodeCairn state: `codecairn init --root <r>`
   followed by `codecairn import <session> --root <r>` (no `--repo-key`)
   followed by `codecairn recall <task> --root <r>` returns the memory —
   repo key derived, config written, index drained.
2. `codecairn.toml` values take effect; the same key via env overrides the
   file; an unknown key aborts startup with a one-line error.
3. `doctor --format text` names each degraded subsystem with a copyable
   remediation command; `--strict` exit code reflects health; JSON output is
   unchanged versus P0.
4. `repos`, `memory show`, `prefetch` work keylessly.
5. `make format` + `make check` green; tests exercise CLI/HTTP surfaces only.

## Out of scope

Interactive TUI prompts, PyPI packaging, MCP, hooks, dashboard, semantic-
compression wiring. No new ADR needed (records an existing stance; ADR 0040
already covers the product-surface direction).
