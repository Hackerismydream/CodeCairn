# One Trace Through CodeCairn

This example is the learner and product acceptance narrative. Identifiers are
illustrative; tests use deterministic fixtures and exact IDs.

## 1. A coding session ends

The user asks Codex:

```text
Make the parser preserve comments, run its focused tests, and leave the
broader refactor for issue #42.
```

The normalized Agent Trace records:

- the exact user message and source location;
- reads of `src/parser.py`;
- a failed `pytest tests/test_parser.py` result;
- a change to `src/parser.py`;
- a later successful focused test;
- an assistant result stating the broad refactor remains.

The Source Layer owns those observations. A model cannot change the command
exit codes or claim another file changed.

## 2. The trace becomes one Episode

The Episode builder assigns one stable Task Episode to the user task and its
related actions. Re-importing an appended transcript reuses the Episode
identity instead of duplicating committed work.

## 3. Capture creates memory

The deterministic path creates exactly one Task Experience:

```yaml
memory_type: task_experience
goal: Preserve parser comments
outcome: success
facets:
  failed_commands: ["pytest tests/test_parser.py"]
  verified_results: ["pytest tests/test_parser.py"]
```

The semantic path may also propose:

- Repository Knowledge: “Parser comments are preserved in the tokenizer
  branch, not reconstructed by the AST renderer.”
- Work State for `issue:42`: “The broader parser refactor remains; next inspect
  the formatter coupling.”

No User Preference is created because the task message contains no reusable
working preference.

If the semantic model is unavailable, the Task Experience is still durable and
the two optional proposals remain a visible pending job.

## 4. Durable commit and projection

The transaction creates immutable Markdown, SQLite mirrors, source checkpoints,
and queue items. The index worker projects active memory into LanceDB. Losing
LanceDB loses no durable memory.

## 5. A later session updates work

The user completes issue #42. Capture creates a terminal Work State with
`workstream_key=issue:42`, `workstream_state=closed`, and the observed outcome.

The model proposes that the new state supersede the old one. CodeCairn verifies
same namespace, type, workstream, active predecessor, and acyclic IDs, then
appends an Evolution Record. The old Markdown file remains unchanged.

## 6. Recall compiles active context

A later agent calls:

```text
recall(task="Change parser formatting without losing comments")
```

CodeCairn:

1. filters to the current repository and active memories;
2. pins the matching issue #42 Work State only when it is open;
3. ranks closed Work State, Repository Knowledge, User Preference, and Task
   Experience;
4. applies type caps and a total context budget;
5. returns Markdown with `codecairn://memory/...` citations;
6. returns a sidecar containing identities, ranks, provenance, and omissions.

The superseded open Work State does not enter normal context. The active closed
state may rank as history but is not pinned as unresolved work.

## 7. History remains explainable

`codecairn memory history <old-state-id>` shows:

```text
old Work State
  --superseded because issue #42 progressed-->
new Work State
```

Restoring the old state creates a third memory revision and a new forward
Evolution Record. It never deletes the newer state or reverses history.

## What each module teaches

| Step | Code area |
|---|---|
| Provider JSONL to normalized events | `importers/` |
| Episode identity and four record types | `memory/` |
| Capture and evolution orchestration | `service/` |
| Markdown/SQLite/LanceDB authority | `storage/` |
| CLI, MCP, hook translation | `entrypoints/` |
| Reproducible proof | `evaluation/` and `evidence/` |

The exact target paths are refined in
[`../plan/tasks/`](../plan/tasks/); this walkthrough stays stable as the
end-to-end behavior contract.
