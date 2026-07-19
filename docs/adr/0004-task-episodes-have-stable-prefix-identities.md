# Task Episodes Have Stable Prefix Identities

Agent Traces are segmented by user-task and action/outcome boundaries. Episode
identity is derived from repository namespace, provider, session, and the
episode's stable opening evidence. It does not include the session's current end
offset.

Appending a later event may extend the active uncommitted episode or create a
new episode, but cannot rename a previously committed episode or memory.
