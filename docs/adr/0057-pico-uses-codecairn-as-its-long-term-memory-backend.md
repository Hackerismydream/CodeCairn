# Pico Uses CodeCairn as Its Long-Term Memory Backend

## Status

Accepted post-v0.1 target. Implementation and live effect remain unverified.

## Context

CodeCairn version 0.1 deliberately shipped independently from an Agent Runtime.
Its initial clients use CLI, MCP, and session-end hooks. Pico already has a
MemoryBackend Interface inside its Agent Runtime, but its shipped memory path
is coupled to EverOS.

The next product milestone needs one real consumer of CodeCairn's repository
memory lifecycle. A sidecar-only MCP connection would leave Pico's built-in
memory path unchanged, preserve two competing sources of long-term memory, and
make memory-off/on evaluation ambiguous. Moving CodeCairn domain or storage
logic into Pico would create a second implementation and reverse the intended
dependency direction.

Pico Session JSONL is also not a safe CodeCairn source. It is owned by Pico and
may be rewritten by history operations. Importing it directly would require
CodeCairn to guess Pico generations and rewrite semantics.

## Decision

CodeCairn will register entry `codecairn` in Pico's `pico.plugins` entry-point
group. The loaded plugin manifest ID is `codecairn-memory`, and its Memory
Backend contribution key is `codecairn`. The plugin implements Pico's
MemoryBackend Interface and calls `CodeCairnApplication`; it does not expose a
second memory model.

The target Pico configuration is JSON:

```json
{
  "memory": {
    "backend": "codecairn"
  }
}
```

CodeCairn derives the Memory Namespace from the explicitly initialized Git
repository. Pico user and agent identifiers do not select the namespace.
Startup fails closed when repository binding, configuration, durable state, or
retrieval state is invalid.

CodeCairn owns an append-only Pico Source Journal under its runtime root. The
journal uses schema `codecairn.pico.source.v1`; one complete record represents
one persisted Pico after-Turn batch. A Pico importer normalizes that source as
provider `pico` while retaining the existing evidence rule: only recognized
structured observations can author roles, command outcomes, file changes, or
verification facts.

The adapter maps Pico user-track recall to repository recall and returns one
compiled Recall Context as a concrete Pico `Memory` with score `0.0` and
explicit compiled-context score semantics. Agent-track recall is empty in
version 0.2. Feedback is a deliberate no-op. Local Skills remain Pico-owned.
Every synchronous CodeCairn operation runs through `asyncio.to_thread`.

The journal's staged append protocol makes crash replay of the same journal
prefix idempotent. Every imported after-Turn batch uses boundary
`pico_turn_end` to close an Episode without claiming task success. Pico
currently supplies no stable caller batch identity, so the adapter does not
claim that two independent identical `store` calls are one operation.

The complete maintained contract is
[`../v0.2/README.md`](../v0.2/README.md).

## Consequences

Positive:

- Pico has one long-term Memory Backend instead of an EverOS path plus a
  CodeCairn sidecar;
- CodeCairn remains the authority for capture, durability, evolution, and
  recall;
- Pico keeps Runtime, Context, Local Skills, Tool/MCP, and evaluation
  ownership;
- installed plugin discovery provides a narrow, testable Seam;
- repository isolation and memory-off/on evaluation have one explicit
  treatment axis;
- CodeCairn can prove a live Agent Runtime integration without becoming an
  Agent Runtime.

Costs and limitations:

- CodeCairn must support a third trace provider and a new source journal;
- Pico and CodeCairn release identities must be recorded together;
- Pico Session may be durable when CodeCairn storage fails, so the integration
  is not one cross-system transaction;
- media understanding and remembered Skill retrieval are not provided by this
  adapter;
- existing EverOS data is not migrated automatically;
- arbitrary repeated store-call deduplication remains deferred until Pico
  supplies a stable batch identity.

## Rejected alternatives

### Use MCP as Pico's hidden memory path

Rejected because it adds transport without replacing Pico's existing backend,
duplicates failure policy, and makes the product treatment axis unclear.

### Import Pico Session JSONL directly

Rejected because Pico owns and may rewrite that file. CodeCairn needs an
append-only source with explicit replay semantics.

### Put the adapter in Pico

Rejected because CodeCairn would lose ownership of source normalization, import
replay, Recall mapping, and package compatibility. Pico should depend on a deep
CodeCairn Integration Module, not duplicate CodeCairn policy.

### Make Pico import CodeCairn internals directly

Rejected because it couples Pico to CodeCairn storage and domain layout instead
of the installed Integration Module. Pico may declare and pin CodeCairn as a
distribution dependency so its default is resolvable, while CodeCairn remains
independently usable and importable without Pico.

### Keep EverOS as an automatic fallback

Rejected because fallback would hide configuration and CodeCairn failures,
reintroduce two memory authorities, and invalidate paired evidence.
