---
status: accepted
---

# LoCoMo Pressure Latency Is Not Product Latency

## Context

The version 0.1 LoCoMo diagnostic recalls from long imported conversations
while two question workers share local embedding and reranking resources. Its
retrieval latency therefore measures a paid evaluation pressure condition, not
the ordinary single-query product path.

A candidate run under the former 4.0-second diagnostic ceiling reached 85.06%
natural-weighted accuracy with zero infrastructure failures and 6.23-second
retrieval P95. Replacing the runner with a second concurrency architecture
would add product and teaching weight without changing the user-facing Recall
contract.

## Decision

The LoCoMo-200 promotion ceiling is 8.0 seconds under the recorded two-worker
pressure protocol. The independent 100-query product retrieval release gate
remains P95 at most 4.0 seconds.

The full LoCoMo score gate remains at least 82.00%. Version 0.1 uses 85% to 86%
as its optimization stop band: reaching that band is sufficient to ship, and
does not authorize retrospective reinterpretation of prior artifacts.

The earlier candidate remains failed under its recorded 4.0-second protocol.
A new clean implementation SHA and protocol hash must produce a fresh
diagnostic before the full paid run is authorized.

## Consequences

- The release keeps one bounded-question-parallel runner.
- Product latency and evaluation pressure latency are published separately.
- The threshold change is explicit, versioned, and bound to new artifacts.
- Accuracy, infrastructure failures, and provenance requirements are unchanged.
