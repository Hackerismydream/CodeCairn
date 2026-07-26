# Index Maintenance Is a Product Surface

## Status

Accepted. It amends the current-state notes in ADR 0006 and ADR 0008, which
recorded that no public entrypoint owned a Mini Cascade sync, retry, or rebuild
operation. The ADR 0006 storage and outbox contract itself is unchanged, and
ADR 0013 and ADR 0015 keep their fail-closed provider semantics.

## Context

ADR 0006 made LanceDB a rebuildable projection fed by a transactional outbox.
Import returns after the atomic Markdown write and the SQLite import/outbox
transaction commit; it does not wait for indexing. `MiniCascade` implemented
the drain, and `run_until_idle()` existed as a service seam — but nothing on
the CLI or HTTP surface called it. Only `evaluation/retrieval.py` and the test
suite did.

On a clean root the documented product flow therefore produced nothing. Import
reported one created memory, `recall` reported that no evidence-backed memory
matched the task, and `doctor` reported an index queue with one pending and
zero indexed revisions at status `degraded`. Draining the queue through the
un-surfaced service method made the same recall return the memory. The gap was
one missing operation, not a retrieval defect.

Every command body also went through the application factory, which
constructed retrieval providers eagerly. `doctor`, `list`, and
`evidence verify` need no embedder, yet all three failed with a provider
traceback when no DashScope key was configured — including the CI step that
verifies a public evidence bundle with no secrets, and the documented claim
that evidence verification needs no provider key.

## Decision

### Index sync, rebuild, and status are public operations

`ApplicationOperations` exposes `sync_index(worker_id, max_jobs)`,
`rebuild_index()`, and `index_status()` as thin delegations to the existing
Mini Cascade. Both entrypoints call the same use-case interfaces, per ADR 0008:

| Operation | CLI | HTTP |
|---|---|---|
| Drain the outbox until idle | `codecairn index sync` | `POST /api/v1/index/sync` |
| Rebuild from durable truth | `codecairn index rebuild` | `POST /api/v1/index/rebuild` |
| Report queue and parity state | `codecairn index status` | `GET /api/v1/index` |

`index rebuild` returns the truth-index parity report rather than a success
flag, because rebuildability is an auditable property of the projection under
ADR 0006 and not an internal repair step. `index status` reads committed state
and resolves no retrieval provider.

### Import drains the queue after its commit, never inside it

`codecairn import` and `POST /api/v1/import` drain the outbox by default, with
`--index/--no-index` on the CLI and the equivalent boolean `index` request
field on HTTP.

The ADR 0006 durability boundary does not move. The import result is computed
and committed first; the drain runs after it and cannot roll it back. A drain
failure is reported as `index` state inside the import payload — `requested`,
`synced`, and the failure type and message — and stays visible through
`doctor`. Import never fails because indexing failed.

### Provider construction is deferred, never substituted

`create_application()` passes a cached `Callable[[], RetrievalProviders]`
inward instead of an eagerly constructed object. Resolution happens on first
retrieval use: import, list, recall, index sync, index rebuild, evaluation.

This does not weaken ADR 0013 or ADR 0015. When retrieval is exercised without
a usable provider, the operation still fails closed with the same contract
error, and no local profile silently stands in for the production embedder.
Only the moment of construction moved. A configuration failure raises the typed
`ProviderConfigurationError`, which the CLI renders as one line plus a
remediation hint instead of a traceback, and which `doctor` records as data on
`providers.retrieval` with `configured` false. `evidence build`,
`evidence verify`, and `eval report` are pure readers and resolve no provider
at all.

## Consequences

- The documented import-then-recall flow works on a fresh root with no manual
  service call.
- Queue depth and truth-index parity are operable from both entrypoints instead
  of being observable only through diagnostics.
- Indexing availability cannot affect import durability; a drain failure is a
  reported state, not a lost import.
- Describing a broken installation no longer requires a working provider, so
  keyless `doctor` and keyless evidence verification both succeed.
- The default drain makes import latency include indexing work. `--no-index`
  restores the previous return-after-commit behavior for bulk imports that
  drain once at the end.
