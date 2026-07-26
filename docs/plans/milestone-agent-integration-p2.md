# Milestone: Agent integration P2 — MCP server and session-end hooks

Parent plan: `everos-alignment-roadmap.md` (product program, steps P2 + P3).
Depends on: usability P0 and onboarding P1 (repo-key derivation, config file).

This milestone closes the loop that makes CodeCairn infrastructure instead of
a CLI: a coding agent recalls memory live during a session (MCP), and each
finished session becomes memory without user action (hooks). Demo target:
finish a Claude Code session, open a new one, ask "have I hit this failure
before?", and watch the agent cite a `content_sha256`-stamped memory it wrote
itself.

## Part A — MCP server

1. **Entry point**: a dedicated console script `codecairn-mcp` (stdio
   transport requires clean stdout, so it must not share the Typer echo
   path). Implemented as `entrypoints/mcp.py` over the same
   `CodeCairnApplication` interfaces — a third presentation adapter under
   ADR 0008, no independent memory behavior.
2. **Tools** (thin, provenance-preserving):
   - `recall(task, repo_key?, limit=5)` — Recall Context markdown plus the
     sidecar as structured content; `repo_key` defaults via the P1 derivation
     helper from the server's working directory,
   - `list_memories(repo_key?)` — compact projection (id, type, title,
     created, evidence count), not the full evidence dump,
   - `import_session(source_path, repo_key?)` — the "remember this session"
     gesture; drains the index by default like the CLI,
   - `index_status()` and `doctor()` — self-diagnosis for the agent.
3. **Resource**: `codecairn://memory/{memory_id}` returns Markdown truth.
   The scheme already appears in recall output (`service/recall.py`), so
   citations become fetchable inside the client.
4. **Dependency**: `mcp` (official Python SDK) as a main dependency; verified
   compatible with the existing pins (httpx>=0.28, uvicorn>=0.32,
   pydantic>=2.9,<3, python >=3.12). If the resolver fights, fall back to an
   optional extra `codecairn[mcp]` and document it.
5. **Registration docs** (README section "Use from Claude Code and Codex"):
   - Claude Code: `claude mcp add codecairn -- codecairn-mcp`
   - Codex: `[mcp_servers.codecairn]` with `command = "codecairn-mcp"` in
     `~/.codex/config.toml`
6. **Failure posture**: provider misconfiguration surfaces as a tool error
   with the same one-line remediation as the CLI — never a stack trace over
   stdio, never a crash of the server process.

## Part B — Session-end auto-import hooks

1. `codecairn hook run` — reads the hook JSON payload on stdin
   (Claude Code `SessionEnd` delivers `{session_id, transcript_path, cwd,
   ...}`), derives the repo key from `cwd`, calls the same
   `import_session` + index drain, and **always exits 0** with bounded
   wall-clock and no stdout noise; failures append to `<root>/hooks.log`.
   Import idempotency (resume from committed cursors, ADR 0011) makes
   repeated firing safe.
2. `codecairn hook install [--claude] [--codex] [--dry-run]` — prints or
   writes the hook entries: Claude Code `SessionEnd` in `settings.json`
   hooks; Codex `~/.codex/hooks.json` `Stop` entry (fires per turn; safe by
   idempotency). `--dry-run` prints the JSON to add without touching user
   config; the default asks the writer path to be explicit rather than
   guessing (never silently edit user config files — print exact snippets
   when the target file cannot be written safely).
3. Skip-list: sessions whose `cwd` resolves inside the runtime root itself.

## ADR

`docs/adr/0043-model-invoked-memory-access-is-an-explicit-audited-surface.md`:
- extends ADR 0008 to the MCP adapter (same use-case interfaces, third
  presentation layer),
- resolves the PRD "live hooks and hidden prompt injection" exclusion: MCP
  tool calls are explicit, client-logged, and provenance-carrying; post-hoc
  session ingestion hooks read a transcript the user already owns; prompt-time
  injection remains out of scope,
- records the failure posture (exit-0 hooks, no-fallback providers).

## Acceptance criteria

1. In-process MCP client tests (the SDK ships one) cover every tool and the
   resource: recall returns markdown + sidecar with provenance; import
   creates then recall finds; keyless recall returns the one-line remediation
   as a tool error; resource fetch returns Markdown truth; nothing writes to
   stdout outside the protocol.
2. `codecairn hook run` with a synthetic payload imports the transcript,
   drains the index, exits 0; malformed payload exits 0 and logs; a second
   run is a no-op (idempotent).
3. `codecairn hook install --dry-run` prints valid JSON snippets for both
   agents; the writing path creates or updates the target without clobbering
   unrelated keys (tested against fixture settings files).
4. ADR 0043 exists; README documents registration for both agents and the
   end-to-end demo flow.
5. `make format` + `make check` green; import-linter contracts hold
   (entrypoints -> service only).

## Out of scope

Prompt-time context injection, SessionStart context loading, the watcher
daemon (`watch --once` remains P3-follow-up), dashboard, packaging.
