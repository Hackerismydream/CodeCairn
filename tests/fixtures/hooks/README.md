# Hook envelope fixtures

These redacted envelopes pin the minimum supported client contracts used by
`v01-007`:

- Claude Code `2.1.220`, `SessionEnd`;
- Codex CLI `0.144.6`, `Stop`.

`__TRANSCRIPT_PATH__` and `__REPOSITORY_PATH__` are test-only placeholders.
The fixtures contain no real home path, repository content, transcript, key, or
conversation. The transcript payloads used by tests remain the separately
reviewable importer fixtures under `tests/fixtures/claude/` and
`tests/fixtures/codex/`.

Codex may send a null `transcript_path`. In that case the supported resolver
requires exactly one owned JSONL source matching the session ID under
`.codex/sessions/<year>/<month>/<day>/`.
