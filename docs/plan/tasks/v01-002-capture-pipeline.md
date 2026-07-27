---
id: v01-002
scope: source-to-memory capture and semantic processing
status: ready
depends-on: [v01-001]
---

# Build one complete capture pipeline

## Objective

Turn every committed Task Episode into exactly one durable Task Experience,
plus optional Knowledge items, without making source durability depend on a
model provider.

## Context

The baseline import path normalizes Codex/Claude traces but automatically emits
only Failed Command memory. The target cardinality is fixed in FR-02/03 and the
semantic provider is optional.

## Paths

Primary:

- `src/codecairn/memory/episode.py`
- `src/codecairn/memory/compression.py` or its small replacement
- `src/codecairn/memory/semantic.py`
- `src/codecairn/service/runtime.py`
- `src/codecairn/service/application.py`
- `src/codecairn/storage/sqlite.py`
- `src/codecairn/storage/markdown.py`
- `src/codecairn/importers/session.py`
- `tests/test_import_session.py`
- `tests/test_compression.py`
- `tests/test_semantic_runtime.py`

## Required changes

1. Build deterministic Task Experience content from the Task Episode's task,
   observed actions, command/file facets, and outcome.
2. Finalize an Episode at the next user task, explicit Stop/SessionEnd, or
   manual-import EOF. Persist boundary kind and stable source span.
3. Use one stable identity per closed Episode and capture schema. Appended
   events after a committed boundary create a linked continuation Episode;
   they cannot replace or duplicate the committed experience.
4. Define a semantic extractor port that proposes Repository Knowledge, User
   Preference, Work State, and evolution relations. It does not rewrite Task
   Experience.
5. Accept User Preference only when cited source events are user-authored.
6. Accept at most one Work State per Episode, only when unresolved work remains
   or the Episode closes an existing open Workstream.
   Its key must be one of the system-derived issue/branch/task/session
   candidates.
7. Commit deterministic memory, source cursor, and semantic job atomically.
8. Implement pending/leased/completed/failed semantic jobs with bounded retry
   and idempotent output fingerprint.
9. Add `process_pending` service behavior for semantic batches. Index draining
   may continue through the existing surface until v01-005 unifies the command.
10. On semantic success, commit optional items and enqueue all new projections
   in one transaction.
11. On semantic absence/failure, keep the Task Experience and expose pending or
    failed status; never report semantic completion.

## Provider contract

Semantic output contains candidate text, type-local fields, source IDs, and
optional `keep_both`/`supersede` proposals for later use. It cannot return
authoritative role, exact quote, command outcome, file change, or verification
state.

## Verification

```bash
uv run pytest \
  tests/test_codex_importer.py \
  tests/test_claude_importer.py \
  tests/test_import_session.py \
  tests/test_compression.py \
  tests/test_semantic_runtime.py
make format
make check
```

Add acceptance cases for:

- success, failure, partial, and unknown Task Experience outcomes;
- an Episode with zero optional Knowledge;
- multiple Knowledge/Preference items and one open Work State;
- an Episode that closes an existing Workstream with a terminal Work State;
- attempted Preference from assistant-authored text;
- no semantic provider;
- provider timeout then successful retry;
- repeated Stop with no new event and appended continuation import;
- an unclosed suffix that produces no memory until finalized;
- transaction failure before and after Markdown creation.

## Exit criteria

- every committed Episode has exactly one Task Experience;
- optional capture obeys cardinality and source-role rules;
- provider absence/failure is visible and retryable;
- no test depends on a real provider;
- all checks pass and line deltas are reported.
