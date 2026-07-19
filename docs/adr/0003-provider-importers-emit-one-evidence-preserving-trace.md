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
