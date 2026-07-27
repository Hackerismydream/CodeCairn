---
id: v01-007
scope: Claude Code and Codex session-end import hooks
status: done
depends-on: [v01-006]
---

# Capture completed agent work through client hooks

## Objective

Make owned Claude Code and Codex sessions enter CodeCairn automatically after
work ends, without prompt injection, a watcher daemon, or client blocking.

## Paths

Primary:

- add `src/codecairn/entrypoints/hooks.py`
- `src/codecairn/entrypoints/cli.py`
- `src/codecairn/service/application.py`
- `src/codecairn/storage/sqlite.py`
- `src/codecairn/bootstrap.py`
- add `tests/fixtures/hooks/`
- add `tests/test_hooks.py`
- `README.md`
- `docs/runtime/operations.md`

## Source fixtures first

Before implementation, capture redacted fixture shapes from supported installed
clients and record their version/source:

- Claude Code `SessionEnd` with `session_id`, `transcript_path`, `cwd`,
  `hook_event_name`, and `reason`;
- Codex `Stop` with the fields required to locate the owned session source.

No fixture may contain a real home path, key, conversation, or repository
content. If Codex does not supply a transcript path, resolution from
`session_id` must be explicit and tested against its supported local layout.
The minimum tested matrix is Codex CLI `0.144.6` Stop and Claude Code
`2.1.220` SessionEnd. Newer release versions are recorded during `v01-010`.

## Required changes

1. Implement client-specific envelope adapters that output a common owned
   source descriptor.
2. Implement `codecairn hook run --client claude|codex` exactly as
   `agent-integration.md` specifies: one stdin JSON value, empty stdout, always
   zero exit, bounded work.
3. Import source, deterministic Task Experience, and index outbox
   synchronously; do not call model providers or drain the full index in the
   hook. `v01-004` recall preflight owns read-your-writes.
4. Record bounded success/failure Hook Receipts in operational state. Include
   client, event, source/session identity digest, repo key, timestamps, outcome,
   error code, and retry hint; exclude content and secrets.
5. Reuse import cursor/idempotency for repeated or per-turn Stop events.
6. Skip a cwd inside the selected runtime root.
7. Implement `hook install --claude|--codex [--dry-run]`:
   parse existing JSON, merge one stable handler, preserve unrelated content,
   write atomically, read back, and become an idempotent no-op.
8. Validate installed client schema/capability before writing. Unsupported
   versions print a dry-run/manual path and make no mutation.
9. `doctor` reports recent failures and exact manual import/retry commands.
10. Document explicit removal and manual import fallback.
11. Configure an explicit five-second client timeout and test cold-start P95:
    at most one second for no-op receipt and four seconds for the release
    fixture. Do not rely on async command hooks or relative cwd.
12. Handle nullable Codex transcript, unsupported versions, and untrusted
    project hooks as visible receipts/remedies, never guessed provenance.

## Failure posture

Hook failure never changes whether the agent stops. Exit zero is intentional,
but it is not silent success: the receipt/log and `doctor --strict` remain
degraded until acknowledged or retried.

## Verification

```bash
uv run pytest tests/test_hooks.py tests/test_import_session.py tests/test_cli.py
make format
make check
```

Acceptance cases:

- valid Claude fixture;
- valid Codex fixture and repeated append;
- malformed/truncated/oversized JSON;
- absent/transient/unsafe transcript;
- unknown repo and uninitialized root;
- source inside runtime root;
- storage failure;
- zero exit and empty stdout for every failure;
- dry-run JSON is parseable;
- install preserves multiple unrelated hooks and file mode;
- second install is byte-identical;
- failed readback leaves recoverable original configuration;
- doctor renders the receipt and remediation.
- hook fixture -> no explicit process -> recall returns the new deterministic
  Task Experience;
- each client fixture repeats 100 times with zero duplicate Episodes/Memories.

Perform one real-client smoke for each installed client before release; unit
fixtures alone do not satisfy v01-010.

## Exit criteria

- both clients auto-import owned traces idempotently;
- no provider call occurs in hook execution;
- external config edits are atomic, verified, and removable;
- failures are non-blocking but operationally visible;
- all checks pass and line deltas are reported.
- product core is at most 10,000 physical Python lines.

## Completion

Implemented on `main` after `v01-006`:

- Claude Code `SessionEnd` and Codex `Stop` normalize to one owned-source
  descriptor; the Codex nullable-transcript fallback is explicit and bounded;
- `hook run` is provider-free, stdout-clean, exit-zero, receipt-backed, and
  reuses incremental import identity;
- `hook install` merges one absolute five-second command atomically, preserves
  unrelated settings and mode, verifies readback, and is byte-idempotent;
- both fixtures repeat 100 times without duplicate Episodes or Memories, and
  hook capture is recallable without an explicit process command;
- production-only test retrieval paths and duplicate serialization/import
  code were removed while compact formatting kept the learning surface within
  the accepted budget.

Verification at the completed worktree:

```text
hook/import/CLI/MCP/lifecycle tests              pass
mypy + import-linter                             pass
source-budget v01-007                            9,998 core / 12,594 total
```

Real installed-client smoke remains a release-candidate gate in `v01-010`; it
is not claimed by fixture acceptance here.
