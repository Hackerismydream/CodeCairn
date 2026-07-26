# Milestone: Usability P0 — make the shipped product work

Parent plan: `everos-alignment-roadmap.md` (product program, step P0).

## Problem, with verified evidence

Two defects make the documented README flow fail end to end. Both were
reproduced on a clean root with a real Claude Code session on 2026-07-26.

**F1 — recall is always empty after import.** `import_session` writes Markdown
truth and enqueues an index-outbox row
(`storage/sqlite.py::_enqueue_index_revision`), then returns. `RecallEngine`
reads only LanceDB. Nothing on the CLI or HTTP surface ever drains the queue —
`MiniCascade.run_until_idle()` (`service/cascade.py`) is called only from
`evaluation/retrieval.py` and tests. Reproduction:

```
$ codecairn import <session>.jsonl --repo-key demo/repo --root <root>
{"created_memory_count": 1, ...}
$ codecairn recall "test command failed" --repo-key demo/repo --root <root> --format markdown
# Recall Context
No evidence-backed memory matched this task.
$ codecairn doctor --root <root>
... "index_queue": {"pending": 1, "indexed": 0}, "status": "degraded"
```

Draining the queue via the un-surfaced service method makes the same recall
return the memory. One missing command is the difference between "product does
nothing" and "product works".

**F2 — nothing runs without a DashScope key.** `create_application()`
(`bootstrap.py`, the eager `create_retrieval_providers()` call near the end of
the factory) constructs retrieval providers unconditionally, and every CLI
command body calls the application factory. `doctor`, `list`, and
`evidence verify` — none of which need an embedder — die with a ~40-line
traceback when no key is set. This also breaks the README claim that evidence
verification "requires no provider key", and the CI workflow step
`uv run codecairn evidence verify evidence/benchmark-v1`, which runs with no
secrets configured.

## Design

### A. Index maintenance surface (fixes F1)

1. `ApplicationOperations` (`service/application.py`) gains three methods,
   each a thin delegation to the existing `MiniCascade`:
   - `sync_index(worker_id: str, max_jobs: int | None) -> IndexHealth`
     (wraps `run_until_idle`)
   - `rebuild_index() -> RebuildReport` (wraps `rebuild`; the parity report is
     the point — rebuilding is an auditability feature per ADR 0006)
   - `index_status() -> IndexHealth` (wraps `health`)
2. CLI: `codecairn index sync|rebuild|status --root ...`, matching the
   existing one-JSON-line output style.
3. HTTP: `POST /api/v1/index/sync`, `POST /api/v1/index/rebuild`,
   `GET /api/v1/index` on the existing app, same error envelope, keeping
   CLI/HTTP parity per ADR 0008.
4. `codecairn import` drains the queue by default after the import commit,
   with `--no-index` to opt out. Semantics guard (per ADR 0006): the outbox
   commit stays the durability boundary — the drain runs after the import
   result is computed, and a drain failure must not fail the import; it
   surfaces as `index` state inside the import output and via `doctor`.
   The HTTP import route gains the equivalent `index: bool = true` field.

### B. Lazy providers with actionable errors (fixes F2)

1. `create_application()` stops building `RetrievalProviders` eagerly; it
   passes a cached factory (`Callable[[], RetrievalProviders]`) inward.
   Resolution happens on first retrieval use (recall, index sync/rebuild,
   eval). ADR 0013/0015 fail-closed semantics are preserved: when retrieval
   *is* exercised without a usable provider, the run still fails closed with
   the same contract error — construction is deferred, never substituted.
2. A typed `ProviderConfigurationError` renders in the CLI as one line plus a
   remediation hint (e.g. `export DASHSCOPE_API_KEY=...` or
   `CODECAIRN_RETRIEVAL_PROFILE=fastembed`), not a traceback.
3. `doctor` reports provider misconfiguration as data
   (`providers.retrieval = {"configured": false, "error": ...}`) instead of
   raising — doctor's job is to describe a broken installation.
4. `evidence verify`, `evidence build`, and `eval report` never construct
   retrieval providers at all (they are pure readers, PRD invariant 6).

## Acceptance criteria

All checkable without a provider key unless stated:

1. Fresh root, hashing/test profile: `import` then `recall` returns the
   imported memory with no manual service calls.
2. `codecairn index status` reports queue and parity state; `index sync`
   drains; `index rebuild` prints a parity report.
3. `env -u DASHSCOPE_API_KEY codecairn doctor --root <r>` exits 0 and reports
   the unconfigured provider as data.
4. `env -u DASHSCOPE_API_KEY codecairn evidence verify evidence/benchmark-v1`
   succeeds (restores the README claim; CI step goes green).
5. `recall` without a key fails closed with a one-line remediation message,
   not a traceback.
6. HTTP surface exposes the three index routes with the standard envelope.
7. `make format` and `make check` green; behavior tests go through CLI, HTTP,
   or service interfaces (AGENTS.md rule), including: import-then-recall
   round-trip, `--no-index` opt-out, drain-failure-does-not-fail-import,
   keyless doctor/verify, and index route contract tests.

## Out of scope

`init` wizard, repo-key derivation, config file, MCP, hooks, watcher,
dashboard, semantic-compression wiring, packaging (steps P1–P6). No changes to
retrieval semantics, gate contracts, or evaluation code paths.

## ADR

One new ADR: "Index maintenance is a product surface and import drains by
default" — records the ADR 0006 outbox-semantics guard and the deferred
(not fallback) provider construction stance relative to ADR 0013/0015.
