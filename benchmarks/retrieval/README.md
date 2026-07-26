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
- deletion and full rebuild of LanceDB with memory-level and complete
  Recall Episode/Atomic Fact document parity.

The shared evaluation entrypoint is available now:

```bash
test -z "$(git status --porcelain=v1 --untracked-files=normal)"
COMMIT="$(git rev-parse --verify HEAD)"
RUN_ID="retrieval-$COMMIT"

codecairn eval run retrieval benchmarks/retrieval \
  --run-id "$RUN_ID" \
  --repository-commit "$COMMIT" \
  --output-root artifacts
codecairn eval report retrieval artifacts/retrieval/"$RUN_ID"
```

Run the structural recovery suite with the checked-in synthetic Codex fixture
and an explicit provider-free test profile:

```bash
RUN_ID="recovery-$COMMIT"
CODECAIRN_RETRIEVAL_PROFILE=hashing-test \
codecairn eval run recovery tests/fixtures/codex/failed_command.jsonl \
  --run-id "$RUN_ID" \
  --repository-commit "$COMMIT" \
  --output-root artifacts
codecairn eval report recovery artifacts/recovery/"$RUN_ID"
```

The recovery suite deletes and rebuilds its own disposable index. Using
`hashing-test` makes the structural check provider-free and deterministic; it
does not validate the production embedding model. A production-profile
recovery run must instead configure that provider explicitly and record its
identity.

The 96.00% Recall@5 row retained in `evidence/benchmark-v3` is a historical run
from commit `fbc7023` using the deterministic HashingEmbedder/RRF composition.
It verifies that frozen suite and artifact reducer; it must not be presented as
a measurement of the current DashScope embedding and local CrossEncoder
production composition. A current-production retrieval claim requires a fresh,
checked-in run whose manifest binds the provider composition and caller-supplied
commit. A release-grade claim must additionally retain a clean-checkout,
dependency-lock, and environment receipt because the retrieval runner does not
prove those fields itself.
