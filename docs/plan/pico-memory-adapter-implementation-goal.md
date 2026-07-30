# Codex Goal: ship the CodeCairn side of Pico Memory

Status: completed historical execution goal. It intentionally stopped before
the joint campaign; current integration and evidence state is maintained in
[`../v0.2/README.md`](../v0.2/README.md).

Document type: historical executable implementation goal.

## Objective

Implement and land the CodeCairn-owned half of the accepted Pico Memory
integration:

```text
Pico after-Turn batch
  -> CodeCairn Pico Source Journal
  -> evidence-preserving Pico importer
  -> CodeCairn Agent Trace and Coding Memory
  -> installed Pico MemoryBackend adapter
  -> repository-scoped Recall Context
```

Finish with a resolvable CodeCairn distribution and machine-readable handoff
that the Pico repository can consume. Do not implement the Pico default switch
or remove EverOS in this repository.

## Authoritative context

Read these files before editing:

1. `AGENTS.md`
2. `CONTEXT.md`
3. `docs/INDEX.md`
4. `docs/v0.2/README.md`
5. `docs/adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md`
6. `docs/plan/tasks/v02-001-pico-trace-import.md`
7. `docs/plan/tasks/v02-002-pico-memory-adapter.md`

The two task files own detailed paths, failure behavior, test matrices, and
exit criteria. This Goal owns their execution order and final handoff.

## Fixed public contract

Do not rename or reinterpret these cross-repository identifiers:

| Contract | Value |
| --- | --- |
| Pico entry-point group | `pico.plugins` |
| Entry-point name | `codecairn` |
| Resource package | `codecairn.integrations.pico` |
| Plugin manifest ID | `codecairn-memory` |
| Memory backend contribution | `codecairn` |
| Backend factory | `codecairn.integrations.pico.backend:make_backend` |
| Source schema | `codecairn.pico.source.v1` |
| Source provider | `pico` |
| Turn boundary | `pico_turn_end` |
| Repository identity | `repo_key` |
| Compiled context score | `0.0` |
| Score semantics | `compiled_context_not_ranked` |

The integration package may lazily import Pico's public `Memory` carrier.
CodeCairn core, service, memory, importer, storage, CLI, MCP, and hook modules
must remain importable without Pico.

## Delivery sequence

Implement the work as two serial, independently reviewable deliveries.

### Delivery 1: `v02-001`

Implement `docs/plan/tasks/v02-001-pico-trace-import.md`.

Required outcome:

- a bounded, append-only, fsynced Pico Source Journal;
- staged-batch recovery and committed-prefix conflict detection;
- provider `pico` normalization into existing Agent Trace contracts;
- `pico_turn_end` closes the first imported Episode without inventing task
  success;
- replaying the same committed prefix creates no duplicate Episode or Coding
  Memory;
- prose that claims success remains untrusted when structured evidence is
  absent;
- existing Codex and Claude import behavior remains unchanged.

Run the task verification, `make format`, and `make check`. Commit, review, and
merge this delivery to the latest `main` before starting Delivery 2.

### Delivery 2: `v02-002`

Create the second delivery from the updated `main`, then implement
`docs/plan/tasks/v02-002-pico-memory-adapter.md`.

Before editing, freeze one immutable Pico compatibility identity:

- repository `Hackerismydream/pico-harness`;
- exact Pico commit and, when available, wheel SHA-256;
- the Plugin discovery and `MemoryBackend` public contracts at that identity.

Use that same Pico identity for implementation decisions, installed tests, and
the final handoff. Do not silently retarget another Pico checkout.

Required outcome:

- the wheel and sdist register entry point `codecairn`;
- `codecairn/integrations/pico/pico-plugin.toml` is packaged;
- the installed plugin contributes exactly one `codecairn` backend;
- `start()` binds `PluginContext.services.workspace`, never process cwd;
- startup requires explicit prior `codecairn init` and fails closed;
- user-track recall returns one concrete Pico `Memory` containing the compiled
  CodeCairn Recall Context;
- agent-track recall returns `[]`;
- `store()` journals, imports, and reaches deterministic read readiness before
  returning;
- `feedback()` is a compatibility no-op;
- blocking CodeCairn calls use `asyncio.to_thread`;
- `stop()` flushes or exposes adapter-owned failure;
- importing CodeCairn core or the resource package does not require Pico;
- Local Skills are neither created nor owned by this adapter.

Test the adapter from installed wheels with source checkouts absent from
`PYTHONPATH`. Run the task verification, `make format`, `make check`, and
`uv build`.

## Required handoff

Delivery 2 must produce a machine-readable handoff containing:

- `schema_version = 1`;
- `kind = "codecairn.pico.adapter.handoff"`;
- CodeCairn commit;
- canonical `install_spec` pinned to the exact 40-character CodeCairn Git
  revision;
- distribution name and version;
- locally built wheel filename and SHA-256;
- Python and platform identity;
- frozen Pico repository, commit, and optional wheel used by the installed
  contract smoke;
- installed entry-point and plugin inventory;
- focused and authoritative check outcomes;
- known limitations from `docs/v0.2/README.md`.

Write the artifact to:

```text
.codecairn/evidence/pico-memory/<codecairn-commit>/handoff.json
```

Do not commit the generated artifact. Copy its redacted summary and SHA-256
into the final Goal response. A local path dependency is not a valid handoff;
the Pico Goal must install from the recorded immutable `install_spec`.

### Post-merge handoff phase

After Delivery 2 squash-merges:

1. fetch the final `origin/main` and freeze the resulting 40-character commit;
2. create or use a clean checkout at exactly that commit;
3. rebuild the wheel and rerun the installed Pico plugin smoke;
4. rerun the required authoritative checks against that source state;
5. generate `handoff.json` under the final commit directory;
6. set `install_spec` to the final `main` commit, never the pre-squash feature
   commit.

If the post-merge checks fail, the Goal is not complete. Fix the failure
through a new reviewed PR, then regenerate the handoff from the new final
`main`.

## Task-state checkpoints

Keep maintained planning state aligned with implementation:

1. the Delivery 1 PR includes the post-merge state `v02-001 = done` and
   `v02-002 = ready`;
2. the Delivery 2 PR includes the post-merge state `v02-002 = done` and keeps
   `v02-003` blocked on Pico `codecairn-002`;
3. each delivery updates `docs/plan/README.md` and `docs/plan/backlog.md` in
   the same atomic merge;
4. no PR merges until its Gates pass, so the implementation and recorded state
   become true together.

## Hard constraints

- Do not modify the Pico repository from this Goal.
- Do not add EverOS compatibility, fallback, dual write, or migration.
- Do not create a Pico-specific memory type or second persistence truth.
- Do not let untrusted text or an LLM author evidence fields.
- Do not move CodeCairn policy into the adapter translation layer.
- Do not advertise fixture, package-discovery, or contract-test success as
  real task-effect evidence.
- Do not begin `v02-003`; it depends on the Pico default switch and EverOS
  removal.
- Do not initiate a paid provider campaign.

## Definition of done

This Goal is complete only when:

1. `v02-001` and `v02-002` are merged serially to CodeCairn `main`;
2. all focused and authoritative checks pass on the final compatible commit;
3. installed-wheel discovery, lifecycle, store, and fresh-process recall pass;
4. CodeCairn remains usable and importable without Pico;
5. the handoff artifact identifies one immutable install specification and
   one locally verified wheel exactly;
6. the final report distinguishes implementation, deterministic evidence, and
   work still blocked on Pico;
7. `v02-003` remains blocked until Pico `codecairn-002` is merged.

Do not stop after writing a plan, opening a branch, or passing focused unit
tests. Finish the two develop-review-merge cycles and return the exact handoff
needed by the Pico repository.
