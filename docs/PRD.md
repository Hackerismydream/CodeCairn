# CodeCairn Version 1

## Problem Statement

Coding agents repeatedly rediscover repository conventions, rerun known-bad
commands, reread the same files, and forget previously verified fixes. Existing
chat histories contain useful evidence, but they are provider-specific, large,
hard to search, and unsafe to treat as trusted summaries. A developer needs a
local runtime that converts those histories into small, inspectable, source-
linked memories and can prove whether those memories improve later coding work.

## Solution

CodeCairn passively imports Codex and Claude Code sessions into one Agent Trace
contract. It segments traces into stable Task Episodes, derives Evidence Facts
from raw events, and allows an LLM to compress only those facts into five typed
Coding Memory proposals. A type-specific Evidence Gate decides what becomes
durable Markdown truth. SQLite records import and indexing state; LanceDB is a
rebuildable hybrid index. The runtime emits task-shaped Recall Context through
shared CLI and HTTP use-case interfaces.

CodeCairn ships its proof alongside the product: LoCoMo end-to-end question
answering, a labeled retrieval set, and isolated memory-on/off coding tasks.
Reports are generated from immutable artifacts rather than hand-entered claims.

## User Stories

1. As a Codex user, I want to import a session JSONL file, so that prior coding work can become reusable memory.
2. As a Claude Code user, I want the same import behavior, so that memory is independent of my coding-agent provider.
3. As a developer, I want malformed JSONL to fail with its source line, so that I can repair or exclude bad input.
4. As an auditor, I want every normalized event to retain its raw location, so that I can inspect the original evidence.
5. As a developer, I want tool calls paired with their results, so that command outcomes are not separated from their actions.
6. As a Codex user, I want `custom_tool_call` and `apply_patch` changes preserved, so that file edits are not silently lost.
7. As a developer, I want sessions segmented by task episodes, so that an unrelated failure does not poison a whole session.
8. As a developer, I want committed episode identities to survive later appends, so that incremental import does not rename existing memories.
9. As a developer, I want imports to continue from a committed cursor, so that a restart does not recompute the whole session.
10. As a developer, I want a failed durable write to leave the cursor unchanged, so that retry cannot skip data.
11. As a developer, I want repeat import to be idempotent, so that I can safely automate ingestion.
12. As a developer, I want repository namespaces included in durable identities, so that identical traces in two repositories remain isolated.
13. As an auditor, I want user quotes to be exact source substrings, so that inferred preferences cannot masquerade as evidence.
14. As an auditor, I want command status derived from tool results, so that an LLM cannot relabel failure as success.
15. As a developer, I want Failed Command memories to cite failed events, so that I avoid repeating verified waste.
16. As a developer, I want Verified Fix memories to require a change and successful verification, so that speculative patches are not recalled as solutions.
17. As a repository maintainer, I want Repository Convention memories grounded in user text or repository rules, so that architectural guidance is defensible.
18. As a developer, I want Debug Episodes to connect task, actions, and outcome, so that future debugging starts from a useful path.
19. As a user, I want stable preferences remembered only from my own words, so that the agent does not invent collaboration rules.
20. As an auditor, I want rejected memory proposals recorded with reasons, so that extraction precision can be measured.
21. As a developer, I want one readable Markdown file per active memory, so that I can inspect and version durable truth.
22. As a developer, I want Markdown writes to be atomic, so that process interruption cannot leave a truncated truth file.
23. As an operator, I want SQLite to expose import cursors and queue state, so that I can diagnose progress without parsing logs.
24. As an operator, I want index workers to claim work atomically, so that concurrent workers do not duplicate embeddings.
25. As an operator, I want unchanged successful content hashes to be no-ops, so that periodic scans do not rebuild everything.
26. As a developer, I want to delete LanceDB and rebuild it from Markdown, so that the search index is never a second source of truth.
27. As a developer, I want lexical and vector candidate sets unioned before ranking, so that exact repository terms are not hidden by vector recall.
28. As an auditor, I want a JSON retrieval sidecar containing candidate sources and scores, so that ranking metrics are reproducible.
29. As a coding-agent user, I want concise Markdown Recall Context for a task, so that I can attach useful memory without dumping the corpus.
30. As a CLI user, I want import, list, recall, eval, and doctor commands, so that the full local loop is scriptable.
31. As a backend reviewer, I want HTTP import, list, recall, evaluation, and health routes, so that the same use cases demonstrate route contracts and error handling.
32. As a maintainer, I want CLI and HTTP to call the same interfaces, so that behavior cannot drift between entrypoints.
33. As an evaluator, I want LoCoMo ingestion to preserve sessions and speakers, so that published QA accuracy follows the dataset structure.
34. As an evaluator, I want repeated LLM-judge votes recorded individually, so that answer accuracy does not hide judge variance.
35. As an evaluator, I want a labeled retrieval set, so that Recall@5 and MRR are measured against independent relevance judgments.
36. As an evaluator, I want 20 coding tasks run with memory on and off three times each, so that usefulness is based on 120 isolated runs.
37. As an evaluator, I want every run to record seed, model, commit, snapshots, tokens, commands, and verifier output, so that results can be reproduced.
38. As an evaluator, I want memory-off runs physically isolated from memory state, so that the comparison cannot be contaminated.
39. As a recruiter, I want reports generated from checked-in manifests and aggregate inputs, so that resume numbers are verifiable.
40. As a maintainer, I want tests for corruption repair, worker interruption, queue replay, concurrent import, and repository isolation, so that recovery claims survive failure injection.
41. As an auditor, I want rebuild parity to cover Recall Episode parents and AtomicFact children, so that a missing child cannot hide behind a matching memory count.
42. As a coding-agent user, I want exact AtomicFact matches lifted to their parent memory, so that a compressed summary cannot hide the detail I asked for.
43. As an auditor, I want query route, hierarchy-level candidates, and matched facts in the sidecar, so that a recall decision can be replayed.
44. As a user, I want bounded chronological neighbors from the same episode, so that a recalled detail retains its immediate context without leaking another repository or task.

## Implementation Decisions

- Python 3.12 is the only supported runtime for version 1.
- The project uses a `src` layout and inward dependency rules enforced by
  import-linter.
- The main import seam is `import_session(source, repo_key) -> ImportResult`.
- Provider Importers retain raw indices and call identifiers and emit one Agent
  Trace contract.
- Task Episode identity uses repository namespace, provider, session, and stable
  opening evidence. It never includes the current session end offset.
- The Import Ledger commits a cursor only after the corresponding Markdown and
  SQLite state are durable.
- The six Coding Memory types are Debug Episode, Conversation Episode,
  Repository Convention, Failed Command, Verified Fix, and User Preference. A
  Conversation Episode keeps exact attributed turns as Evidence Facts and a
  separately marked semantic retrieval projection grounded in those facts.
- Evidence Facts are derived by code. The LLM may reference fact identifiers and
  author summaries, but cannot author provenance fields.
- Markdown truth uses same-directory temporary files, flush, fsync, atomic
  replace, and containment checks. It stores complete deterministic fact
  snapshots with each Coding Memory.
- SQLite owns import state, audit rows, memory metadata, and a transactional
  index outbox whose uniqueness includes repository namespace.
- LanceDB is mandatory in the completed version 1 but is never authoritative.
  It projects each Coding Memory into one Recall Episode parent plus its
  AtomicFact children.
- Hybrid retrieval searches Episode and AtomicFact projections independently,
  max-pools child hits to their parents, and unions four lexical and learned-vector
  rankings before a CrossEncoder reranker. A deterministic soft route changes pool
  sizes but never hard-disables the secondary level. Logical model aliases, artifact repositories, immutable
  commit revisions, dimensions, and Adapter versions are recorded in index rows
  and evaluation artifacts; hashing is a test-only Adapter.
- Recall Context is Markdown first with a structured JSON sidecar.
- CLI and HTTP are presentation adapters over shared use-case interfaces.
- Evaluation uses immutable suite, task, and run manifests. Report generation is
  pure and cannot rebuild or overwrite a runtime index.
- Public fixtures are synthetic. Private real traces remain outside Git history
  and are referenced by hash-only manifests.

## Testing Decisions

- Tests exercise behavior through use-case interfaces, CLI invocations, or HTTP
  routes rather than private helpers.
- Each tracer bullet is implemented as one failing contract test followed by the
  minimum implementation and refactoring while green.
- Import tests use literal synthetic JSONL records and independently specified
  expected events, identities, and evidence facts.
- Failure injection covers malformed input, append-after-import, mid-write
  interruption, corrupt Markdown, duplicate import, cross-repository import,
  lease contention, and index deletion.
- Evidence Gate tests include adversarial proposals with invented quotes,
  changed roles, mismatched commands, and failed verification.
- Retrieval evaluation uses fixed relevance labels, not titles copied from the
  expected memory and not repository membership as a relevance shortcut.
- Coding-task verifiers execute commands inside isolated workspaces; manually
  supplied verifier JSON is not accepted as proof.
- The authoritative local gate is formatting, lint, strict type checking,
  dependency contracts, tests, and coverage reporting.

## Out of Scope

- Running or wrapping Codex and Claude Code during ordinary product use.
- Live hooks and hidden prompt injection.
- Authentication, multi-user cloud service, billing, or organization tenancy.
- Dashboard and memory editing UI.
- General document ingestion, multimodal memory, profile evolution, and agent
  skill synthesis.
- Distributed workers and cross-host queue leases.
- Publishing benchmark targets before real runs complete.

## Further Notes

The intended resume artifact is the generated evidence bundle, not a prose claim:
session and event counts, extraction labels, retrieval query results, LoCoMo
answers and judge votes, 120 coding-run manifests, recovery checks, coverage,
and the exact commands required to reproduce aggregates.
