---
id: v02-001
scope: Pico Source Journal and Agent Trace import
status: ready
depends-on: []
---

# Import Pico turns as evidence-preserving Agent Traces

## Objective

Create the durable source and importer required for Pico capture without
depending on Pico Session rewrite semantics or weakening CodeCairn evidence
rules.

## Contract

Read:

- [`../../v0.2/README.md`](../../v0.2/README.md);
- [`../../adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md`](../../adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md);
- [`../../adr/0003-provider-importers-emit-one-evidence-preserving-trace.md`](../../adr/0003-provider-importers-emit-one-evidence-preserving-trace.md);
- [`../../adr/0005-evidence-fields-are-derived-not-generated.md`](../../adr/0005-evidence-fields-are-derived-not-generated.md);
- [`../../adr/0011-import-resume-replays-only-the-active-suffix.md`](../../adr/0011-import-resume-replays-only-the-active-suffix.md).

The source schema is `codecairn.pico.source.v1`. The normalized provider is
`pico`.

## Paths

Primary:

- add `src/codecairn/integrations/__init__.py`;
- add `src/codecairn/integrations/pico/__init__.py`;
- add `src/codecairn/integrations/pico/journal.py`;
- add `src/codecairn/importers/pico.py`;
- `src/codecairn/importers/session.py`;
- `src/codecairn/memory/episode.py`;
- `src/codecairn/memory/schema.py`;
- `src/codecairn/memory/models.py`;
- `src/codecairn/service/runtime.py`;
- `src/codecairn/storage/sqlite.py`;
- add `tests/test_pico_source_journal.py`;
- add `tests/test_pico_importer.py`.

Exact files may change after current-code inspection, but the dependency
direction may not: importer and Integration Module call service/memory
contracts and never entrypoints.

## Required changes

1. Extend the closed Source Layer provider vocabulary with `pico`.
2. Implement the safe hashed source path under the CodeCairn runtime root.
3. Write one header and one canonical JSONL record per persisted after-Turn
   batch.
4. Bound and validate every identity, event collection, text field, tool
   argument, tool result, and optional terminal observation.
5. Implement the staged, fsynced, per-journal-lock append protocol from the
   version 0.2 contract.
6. Recover a staged complete batch, repair only an uncommitted unterminated
   final fragment, and reject a conflicting committed prefix.
7. Detect the Pico schema and normalize it into one Agent Trace.
8. Add boundary kind `pico_turn_end` and use it for every complete imported
   after-Turn batch so the first batch closes an Episode without asserting a
   successful task outcome.
9. Preserve exact role, text, source digest, source index, call identifier, and
   recognized structured tool fields.
10. Pair tool calls and results only through matching structured call IDs.
11. Derive command, exit status, file change, or verification evidence only
    from a closed recognized structured field. Text is never proof.
12. Reuse the existing Import Ledger, episode, Write Intent, index queue, and
    Recall lifecycle. Do not add a Pico-specific memory type or persistence
    path.
13. Keep journal replay idempotent for the same prefix. Record in code and tests
    that a second independent identical append is a second batch.
14. Preserve existing Codex and Claude import behavior and every historical
    artifact verifier.

## Source acceptance matrix

The checked-in fixtures must cover:

- user, assistant, system, and tool messages;
- one matched tool call/result pair;
- unmatched and duplicate call identifiers;
- structured failed and successful tool-result status when supplied;
- tool results without a structured status;
- tool text that claims success without structured status;
- assistant text that claims tests passed;
- unknown event fields;
- multiple turns in one session;
- two sessions in one repository;
- the same session-shaped identifier in two repositories;
- partial final write and staged recovery;
- committed-prefix mutation and truncation;
- oversized and malformed records.

Fixtures are synthetic and redacted. They are contract evidence, not live Pico
evidence.

## Verification

Run:

```bash
uv run pytest tests/test_pico_source_journal.py tests/test_pico_importer.py tests/test_import_session.py
make format
make check
```

Acceptance:

- the first persisted after-Turn batch produces one stable source suffix and
  one `pico_turn_end` Episode;
- replaying the same journal prefix creates no duplicate Episode or Coding
  Memory;
- appending a second turn creates only the new suffix work;
- crash recovery reuses the staged `batch_id` and bytes;
- conflicting bytes fail before a source cursor advances;
- a second independent identical append is observable as a second batch;
- unstructured success prose yields outcome `unknown`;
- Evidence References resolve to the exact Pico journal records;
- source/import cursors agree after every committed batch;
- existing Codex and Claude importer tests remain unchanged and pass.

## Exit criteria

- the source journal and importer are complete without Pico installed;
- provider `pico` follows the same Agent Trace and evidence invariants as the
  existing providers;
- journal recovery and import replay have fault tests;
- no public claim describes fixtures as a real Pico integration;
- all authoritative checks pass.
