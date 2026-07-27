# Version 0.1 Learning Path

CodeCairn is intentionally designed to be learned from the outside in. Finish
each stop before opening the next one.

## 1. Product and language

Read:

1. [`../../README.md`](../../README.md)
2. [`../../CONTEXT.md`](../../CONTEXT.md)
3. [`README.md`](README.md)
4. [`../runtime/installation.md`](../runtime/installation.md)

You should be able to answer: what does CodeCairn own, what remains in the
coding agent, and why are Source, Memory, Evolution, and Recall different
authorities?

## 2. One complete behavior

Read [`walkthrough.md`](walkthrough.md), then run:

```bash
make eval-smoke
```

Trace one fixture from client JSONL to source events, one Task Experience,
optional Knowledge, an Evolution Record, active recall, and history.

## 3. Domain before adapters

Read:

1. [`schema-contract.md`](schema-contract.md)
2. [`memory-lifecycle.md`](memory-lifecycle.md)
3. `src/codecairn/memory/`
4. domain tests for model and lifecycle invariants

Look for closed enums, stable identities, append-only records, and validation
that does not import storage or entrypoints.

## 4. Use cases

Read `src/codecairn/service/` and its public-behavior tests. Follow these three
flows only:

```text
import/capture
evolve/restore
recall/history
```

Ignore evaluation until those flows are clear.

## 5. Persistence

Read:

1. Markdown adapter;
2. SQLite adapter, Write Intents, recovery, and queue transactions;
3. LanceDB projection and rebuild;
4. [`../runtime/operations.md`](../runtime/operations.md).

Be able to explain why Markdown is truth, SQLite is operational state, and
LanceDB is disposable.

## 6. Product adapters

Read [`agent-integration.md`](agent-integration.md), then:

1. CLI adapter;
2. MCP adapter;
3. Claude and Codex hook envelope adapters;
4. bootstrap composition.

All paths should terminate in the same service operations. If an entrypoint
contains memory policy, the boundary has leaked.

Compare the visible client workflow and agent wording in
[`../runtime/installation.md`](../runtime/installation.md) and
[`../runtime/agent-instructions.md`](../runtime/agent-instructions.md) with
the adapter code. Setup never grants hidden trust or injects instructions.

## 7. Evaluation and evidence

Read:

1. [`evaluation-and-release.md`](evaluation-and-release.md)
2. [`../evaluation/README.md`](../evaluation/README.md)
3. [`../evidence-bundle.md`](../evidence-bundle.md)

Distinguish an offline verifier from a benchmark rerun, a deterministic smoke
from a score, and an infrastructure failure from a task failure.

## 8. Why the design changed

Use [`../adr/README.md`](../adr/README.md):

- ADRs 0001–0011 explain the foundation;
- ADRs 0012–0042 explain the implemented retrieval and evidence history;
- ADRs 0043–0052 define the version 0.1 simplification and product.

ADRs are causal history. Maintained design documents own the current target.

## Contribution rule

Start from one file in [`../plan/tasks/`](../plan/tasks/), then follow
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md). A task is complete
only when its public verification passes and the maintained docs describe the
actual behavior. Do not implement a deferred feature because a historical ADR
or benchmark helper happens to mention it.
