# Live Hub decision

The version 0.3 implementation keeps only Memories, Recall, and System.

- Memories reads real pages, details, Evidence Facts, and evolution history.
- Recall has one task form because it now invokes the real Memory OS.
- System is one point-in-time Doctor read and does not imply a daemon.
- Missing data remains empty. A failed connection remains failed.
- There are no governance, remote, account, activity, or roadmap controls.

The checked-in contract example verifies service semantics; it is not a
runtime data source for React.
