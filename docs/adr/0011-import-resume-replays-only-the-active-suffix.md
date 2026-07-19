# Import Resume Replays Only the Active Suffix

`import_session(source, repo_key)` remains the only public import interface.
Internally, the Import Ledger stores a resume checkpoint at the opening raw
event of the last Task Episode. Events before that opening form the stable
prefix; the active suffix may still extend when a session is appended.

The checkpoint retains a chain digest of the stable raw-event prefix, its call
identifiers, and its file-change fact count. A resumed import scans record
boundaries and validates that digest without JSON-decoding stable events. It
normalizes and extracts only the active suffix. Source truncation before the
committed cursor, prefix mutation, duplicate call identifiers, and a backwards
cursor update are rejected before the ledger can advance.

Before importing a suffix, the runtime reconciles every committed memory with
its Markdown artifact. Missing, truncated, unparsable, or hash-mismatched files
are rebuilt only when the SQLite committed record deterministically renders to
the expected content hash. Recovery uses an atomic replace followed by a
parse-and-hash readback.

Each repair has a two-phase SQLite audit record. `started` records survive an
interruption and are resumed on the next import; repair failure becomes
`failed`, and verified completion becomes `completed`. Re-running against
healthy Markdown is a no-op and creates no new audit record. Concurrent
repairers coalesce on the active audit, and repeated completion is idempotent.
SQLite is recovery material in this path, not a second editable truth source.
