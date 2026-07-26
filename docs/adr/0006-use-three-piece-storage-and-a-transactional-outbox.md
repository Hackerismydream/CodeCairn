# Use Three-Piece Storage and a Transactional Outbox

## Status

Accepted and implemented as a service contract. Current public CLI and server
composition commit the outbox but do not own a Mini Cascade worker or expose a
supported sync/retry/rebuild command.

CodeCairn uses Markdown as truth, SQLite as operational state, and LanceDB as a
required rebuildable index. Import returns after atomic Markdown persistence and
the SQLite import/outbox transaction commit. It does not wait for indexing.

Queue workers use atomic leases. A successful row with the same content hash is
a no-op. Recovery compares actual Markdown hashes rather than historical paths.
