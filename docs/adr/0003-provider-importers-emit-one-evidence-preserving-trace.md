# Provider Importers Emit One Evidence-Preserving Trace

Codex and Claude Code importers emit the same Agent Trace contract. Every
normalized event retains provider, session, source hash, raw event index, call
identifier, and event kind. Tool call and result records are paired without
discarding either raw evidence location.

Codex support includes both `function_call` and `custom_tool_call`, including
`apply_patch` file-change events.

The importer parses `apply_patch` envelopes as data and never executes them.
Each add, update, delete, or move becomes a `FileChangeFact` whose identity and
Evidence Reference point to the custom call record. A custom output is paired
only with a custom call carrying the same unique call identifier; unmatched or
protocol-mismatched outputs cannot claim that call's tool name or facts.
Only a result paired to a known command-result tool can contribute to an
episode outcome. This provenance includes `exec_command` and the `write_stdin`
calls that observe long-running command completion; unrelated function outputs
cannot author outcomes.
Patch paths, including absolute or parent-relative paths found in real Codex
traces, remain exact evidence strings and are never used as filesystem targets.

Claude Code support reads its user and assistant message envelopes, flattens
text, `tool_use`, and `tool_result` blocks into the same event contract, and
pairs them by `tool_use_id`. A paired `Bash` result derives command outcome from
the recorded result envelope. Successful `Write`, `Edit`, and `MultiEdit`
results derive file-change facts from their structured result without treating
tool input alone as proof that a change occurred.

The composition root uses a provider router. A new source is detected from its
JSONL envelope; a resumed source uses the provider persisted in its checkpoint.
Provider choice never enters extraction, persistence, CLI, or HTTP behavior.
