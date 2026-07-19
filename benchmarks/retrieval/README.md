# Retrieval and Recovery Evaluation

This suite evaluates the hybrid Recall Context path independently from LoCoMo
answer generation. Its checked-in, non-sensitive inputs contain 20 repository
rules and 100 separately authored queries across two repository namespaces.

Each query names one or more relevant corpus keys. A repository namespace is
only a filter: the evaluator rejects a label set that marks every memory in the
repository as relevant. Generated memory titles are generic identifiers and
the evaluator rejects a query that copies the title of its relevant memory.

The runner writes an immutable artifact for every query with the vector and
lexical candidate sources, component ranks and scores, final rank, content
digest, and measured latency. The read-only report computes Recall@1,
Recall@5, MRR, irrelevant-context rate at five, P95 latency, and repository
isolation violations from those artifacts.

The separate storage-recovery run uses a synthetic Codex fixture and verifies:

- repeated import idempotency;
- cross-repository memory identity isolation;
- append resume from the active task suffix;
- takeover of an expired queue lease;
- detection of corrupted Markdown by its actual digest; and
- deletion and full rebuild of LanceDB with memory-id/content-hash parity.

The shared `codecairn eval` command and the final evidence bundle provide the
exact run and aggregation commands once the public entrypoint lands.
