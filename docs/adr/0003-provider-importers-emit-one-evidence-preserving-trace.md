# Provider Importers Emit One Evidence-Preserving Trace

Codex and Claude Code importers emit the same Agent Trace contract. Every
normalized event retains provider, session, source hash, raw event index, call
identifier, and event kind. Tool call and result records are paired without
discarding either raw evidence location.

Codex support includes both `function_call` and `custom_tool_call`, including
`apply_patch` file-change events.
