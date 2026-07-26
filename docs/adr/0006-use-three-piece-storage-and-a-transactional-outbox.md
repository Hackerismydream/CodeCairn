# Use Three-Piece Storage and a Transactional Outbox

## Status

Accepted and amended. The storage and outbox contract below is implemented and
unchanged. ADR 0040 replaces the note that no public entrypoint exposed a
supported sync or rebuild command: the CLI and HTTP surface now own index sync,
rebuild, and status, and import drains the outbox after its commit. Neither
entrypoint starts a background cascade worker.

CodeCairn uses Markdown as truth, SQLite as operational state, and LanceDB as a
required rebuildable index. Import returns after atomic Markdown persistence and
the SQLite import/outbox transaction commit. It does not wait for indexing.

Queue workers use atomic leases. A successful row with the same content hash is
a no-op. Recovery compares actual Markdown hashes rather than historical paths.
