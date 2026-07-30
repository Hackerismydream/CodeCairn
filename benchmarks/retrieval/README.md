# Retrieval and Recovery Evaluation

This suite evaluates the hybrid Recall Context path independently from LoCoMo
answer generation. Its checked-in, non-sensitive inputs contain 20 repository
rules, 100 separately authored positive queries, and 20 unrelated
hard-negative queries across two repository namespaces.

Each query names one or more relevant corpus keys. A repository namespace is
only a filter: the evaluator rejects a label set that marks every memory in the
repository as relevant. Generated memory titles are generic identifiers and
the evaluator rejects a query that copies the title of its relevant memory.

The runner writes an immutable artifact for every query with the vector and
lexical candidate sources, component ranks and scores, final rank, content
digest, and measured latency. The read-only report computes Recall@1,
Recall@5, MRR, irrelevant-context rate at five, P95 latency, and repository
isolation violations from those artifacts. It also reports the proportion of
hard negatives that received any memory and their mean injected-item count.
The gate requires both hard-negative metrics to be at most 5%.

The separate storage-recovery run uses a synthetic Codex fixture and verifies:

- repeated import idempotency;
- cross-repository memory identity isolation;
- append resume from the active task suffix;
- takeover of an expired queue lease;
- detection of corrupted Markdown by its actual digest; and
- deletion and full rebuild of LanceDB with memory-level and complete
  Recall Episode/Atomic Fact document parity.

The v0.1 entrypoint runs the current runtime with named deterministic
evaluation-only retrieval adapters:

```bash
make eval-retrieval
RUN_ID="retrieval-$(git rev-parse --short HEAD)" make eval-retrieval
```

Without `RUN_ID`, the isolated run is temporary and prints its aggregate.
With a run ID it writes an exclusive manifest, per-query outcomes, and
aggregate under `benchmark_results/retrieval/<RUN_ID>`. Protocol
`codecairn-retrieval-120-v02` validates current relevance admission, Recall
Engine behavior, and context compilation through deterministic
evaluation-only providers; it is not a production embedding-model score.

The 96.00% Recall@5 row retained in `evidence/benchmark-v3` is a historical run
from commit `fbc7023` using the deterministic HashingEmbedder/RRF composition.
It verifies that frozen suite and artifact reducer; it must not be presented as
a measurement of the current DashScope embedding and local CrossEncoder
production composition. A current-production retrieval claim requires a fresh,
checked-in run whose manifest binds the provider composition and caller-supplied
commit. A release-grade claim must additionally retain a clean-checkout,
dependency-lock, and environment receipt because the retrieval runner does not
prove those fields itself.
