# Security

CodeCairn processes coding-agent traces, which may contain source code,
commands, paths, and model responses. Treat runtime roots, namespace exports,
hook receipts, and evaluation artifacts as sensitive local data. Do not attach
real private traces, credentials, or unredacted runtime roots to a public
issue.

Supported security fixes target the latest tagged release and current `main`.
Pre-release snapshots and historical benchmark runners are not separate
supported branches.

If GitHub shows a private **Report a vulnerability** action for this
repository, use it. If that action is unavailable, open a minimal issue asking
the repository owner for a private reporting route and include no exploit,
secret, or private trace. The project does not promise an email hotline,
response SLA, bug bounty, or private support channel that is not listed in the
repository.

Reports should identify the affected version or commit, entrypoint, impact,
and a minimal redacted reproduction. Provider credentials must remain in the
reporter's environment and must never be committed.
