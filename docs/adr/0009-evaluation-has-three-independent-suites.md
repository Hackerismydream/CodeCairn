# Evaluation Has Three Independent Suites

CodeCairn reports three distinct suites:

1. LoCoMo end-to-end answer accuracy with category breakdown and repeated judge
   votes.
2. Retrieval-set Recall@k, MRR, latency, isolation, and rebuild consistency.
3. Isolated coding-task memory-on/off runs with task pass rate, repeated reads,
   repeated failures, tokens, and cost.

Every run has an immutable run identifier, repeat number, seed, model and tool
configuration, repository commit, workspace snapshot, memory snapshot, and raw
artifact references. Report generation is read-only.
