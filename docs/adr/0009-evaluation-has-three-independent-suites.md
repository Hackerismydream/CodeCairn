# Evaluation Has Four Independent Suites

## Status

Accepted and amended by implementation. Recovery is a separately runnable
fourth suite rather than a retrieval sub-check.

CodeCairn reports four distinct suites:

1. LoCoMo end-to-end answer accuracy with category breakdown and repeated judge
   votes.
2. Retrieval-set Recall@k, MRR, latency, and isolation.
3. Isolated coding-task memory-on/off runs with task pass rate, repeated reads,
   repeated failures, tokens, and cost.
4. Synthetic recovery checks for idempotency, resume, lease takeover, truth
   corruption, and rebuild parity.

Every run has an immutable run identifier, explicit inputs and output
directory, and a suite-appropriate manifest. Newer protocols bind the
applicable commit, selection, provider, seed, repeat, workspace, memory,
corpus, resource, and raw-artifact fields; a field that does not apply to a
suite is not invented. Historical source artifacts may have weaker manifests,
and their provenance limits must remain visible. Report generation is
read-only.
