---
id: v02-002
scope: installed Pico MemoryBackend adapter
status: blocked
depends-on: [v02-001]
---

# Expose CodeCairn as an installed Pico Memory Backend

## Objective

Ship one deep Integration Module that Pico can discover as
`memory.backend = "codecairn"` without moving CodeCairn policy into Pico or
adding Pico to CodeCairn core modules.

## Preconditions

- `v02-001` is on `main`;
- the exact Pico plugin and MemoryBackend contracts are recorded from the Pico
  integration commit;
- a Pico wheel or immutable source commit is available for installed tests.

## Contract

Read:

- [`../../v0.2/README.md`](../../v0.2/README.md);
- [`../../adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md`](../../adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md);
- current Pico plugin discovery, plugin manifest, and MemoryBackend Interface
  at the pinned Pico commit.

## Paths

Primary:

- `src/codecairn/integrations/pico/__init__.py`;
- add `src/codecairn/integrations/pico/pico-plugin.toml`;
- add `src/codecairn/integrations/pico/backend.py`;
- `src/codecairn/integrations/pico/journal.py`;
- `src/codecairn/bootstrap.py`;
- `pyproject.toml`;
- add `tests/test_pico_memory_adapter.py`;
- add an installed-wheel Pico plugin smoke;
- `README.md`;
- `docs/runtime/installation.md`;
- `docs/runtime/operations.md`.

## Required changes

1. Register entry `codecairn` in Pico's `pico.plugins` entry-point group,
   expose plugin manifest ID `codecairn-memory`, and contribute Memory Backend
   key `codecairn`.
2. Point the entry value at resource package `codecairn.integrations.pico`,
   keep that package's `__init__.py` import-cheap, include
   `pico-plugin.toml` in both wheel and sdist, and point its factory at
   `codecairn.integrations.pico.backend:make_backend`.
3. Implement the backend structurally, but lazily import Pico's public
   `Memory` carrier when creating recall hits so Pico's concrete-result
   contract passes. CodeCairn memory, service, importers, storage, bootstrap,
   CLI, MCP, and hooks must not import Pico, and importing CodeCairn core or
   the entry-point resource package must not import Pico.
4. Construct the adapter through maintained CodeCairn configuration and
   `CodeCairnApplication`, not concrete storage objects.
5. On `start`, resolve the Git repository from
   `PluginContext.services.workspace`, never process cwd, require prior
   `codecairn init`, validate repository identity and retrieval configuration,
   recover staged journal work, and fail closed on any mismatch.
6. Map user-track recall to repository recall. Return one concrete Pico
   `Memory` containing the compiled Recall Context plus maintained sidecar
   fields. Set `score=0.0` and
   `metadata["score_semantics"]="compiled_context_not_ranked"`; do not invent
   a score for packed context.
7. Return an empty result for agent-track recall. Do not expose Coding Memories
   as remembered Pico Skills.
8. Map `top_k` to recall `limit` and reject invalid values.
9. On `store`, append one normalized after-Turn batch through the Pico Source
   Journal, import the resulting suffix with `pico_turn_end`, and require
   deterministic read readiness before returning success.
10. Make `feedback` a compatibility no-op with no hidden provider or
    persistence call; do not require it in the live campaign because Pico's
    current Skill feedback path is EverOS-qualified.
11. Expose no media-understanding capability.
12. Use `asyncio.to_thread` for every synchronous CodeCairn operation that may
    block Pico's event loop.
13. On `stop`, complete or surface adapter-owned staged work. Do not drain
    unrelated semantic work indefinitely.
14. Translate CodeCairn typed failures into stable Pico backend failures without
    returning empty fake success, raw stack traces, paths, or secrets.
15. Extend import-linter contracts so core modules cannot depend on the Pico
    Integration Module while that Module may depend inward on public service
    and importer seams.
16. Build and test the adapter from installed wheels with source checkouts
    absent from `PYTHONPATH`.

## Failure posture

The adapter has no fallback to EverOS or a global Memory Namespace.

| Failure | Required result |
|---|---|
| Not in a Git repository | startup fails with initialization remediation |
| CodeCairn not initialized | startup fails and prints `codecairn init` remediation |
| Repository identity mismatch | startup fails before recall or store |
| Retrieval profile/index mismatch | typed configuration or freshness failure |
| Journal/import failure after Pico Session save | adapter raises; Pico must not report clean memory-backed completion |
| Recall cannot reach required cursor | `index_not_ready`, never stale or empty success |
| Agent-track recall | explicit empty supported result |

## Verification

Run focused tests followed by:

```bash
make format
make check
uv build
```

Installed acceptance must prove:

- a fresh environment discovers entry `codecairn`, manifest ID
  `codecairn-memory`, and exactly one `codecairn` Memory Backend contribution;
- wheel inspection proves `codecairn/integrations/pico/pico-plugin.toml` is
  packaged and the entry-point value resolves to that resource package;
- the adapter loads from the wheel with no source checkout import;
- importing `codecairn`, CodeCairn core, and
  `codecairn.integrations.pico` succeeds without Pico installed;
- Pico and CodeCairn core import direction remains valid;
- missing initialization and wrong repository fail before Agent execution;
- a test where process cwd differs from Pico Workspace binds the Workspace
  repository;
- one store followed by recall returns one concrete Pico `Memory` with
  `score=0.0`, explicit compiled-context score semantics, and one compiled
  context;
- a new Pico process recalls the committed memory;
- the sidecar contains rendered IDs, source URIs, freshness, cursors, and
  retrieval profile from CodeCairn;
- agent-track recall is empty and feedback has no side effects;
- one slow CodeCairn call does not block an independent Pico event-loop task;
- Local Skills are not created, changed, or loaded by the adapter;
- no EverOS package is required by the CodeCairn wheel.

## Handoff artifact

Produce a machine-readable handoff containing:

- CodeCairn commit;
- resolvable CodeCairn distribution name and version;
- wheel filename and SHA-256;
- Python and platform identity;
- Pico commit/wheel used by installed smoke;
- plugin entry-point inventory;
- focused and authoritative check outcomes;
- known limitations from the version 0.2 contract.

This handoff is package evidence, not task-effect evidence.
It must reference a published or otherwise resolvable versioned wheel. A local
path dependency is not an acceptable Pico default.

## Exit criteria

- the installed adapter satisfies the Pico MemoryBackend Interface;
- CodeCairn remains usable without Pico;
- the Integration Module contains translation only and delegates memory policy
  to `CodeCairnApplication`;
- all installed, dependency-direction, and authoritative checks pass;
- Pico receives an exact wheel identity for its default-switch task.
