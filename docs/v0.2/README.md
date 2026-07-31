# Version 0.2 Pico Memory Integration

Status: CodeCairn deliveries `v02-001` and `v02-002`, Pico's default switch,
and EverOS product-coupling removal are implemented. The first joint campaign
completed all 32 trials and all 16 paired tasks, but its positive-effect claim
was ineligible because every hard-negative query received memory. ADR 0060
adds auditable relevance admission; the current release pair still needs a
joint rerun. Fixture and package-contract tests remain distinct from live
integration evidence.

## Outcome

Version 0.2 makes CodeCairn the long-term Memory Backend used by Pico:

```text
Pico Runtime
  -> Pico MemoryBackend Interface
     -> CodeCairn Pico Memory Adapter
        -> CodeCairnApplication
           -> Pico Source Journal
           -> Pico Agent Trace
           -> Coding Memory
           -> Recall Context
```

The user-facing Pico configuration is JSON:

```json
{
  "memory": {
    "backend": "codecairn"
  }
}
```

The comparison configuration is:

```json
{
  "memory": {
    "backend": null
  }
}
```

This is a direct Memory Backend replacement. It is not an MCP sidecar, prompt
wrapper, or second Agent Runtime.

## Scope

Version 0.2 includes:

- one installed Pico plugin whose manifest ID is `codecairn-memory`;
- one CodeCairn-owned adapter for Pico's MemoryBackend Interface;
- one append-only source format, `codecairn.pico.source.v1`;
- normalization of Pico turn records into provider `pico` Agent Traces;
- repository-derived Memory Namespace selection;
- durable capture of persisted Pico after-Turn slices;
- idempotent delivery of verifier-backed Coding Task outcomes with a stable caller key;
- active CodeCairn recall injected through Pico's user-memory track;
- installed, cross-process, cross-repository, and paired A/B evidence.

Version 0.2 does not include:

- a general integration framework for arbitrary Agent Runtimes;
- replacing Pico's Local Skills;
- returning CodeCairn memories through Pico's agent-memory track;
- adapting CodeCairn to Pico's Skill Interface;
- using MCP between Pico and CodeCairn;
- automatic migration or deletion of existing EverOS data;
- media understanding through the memory adapter;
- duplicate-write suppression for ordinary `store` calls without a stable caller identity;
- positive task-effect claims before a commit-bound paired evaluation passes.

## Ownership and dependency direction

CodeCairn owns the deep Integration Module:

- the `pico.plugins` package entry point;
- the Pico Memory Adapter;
- repository identity resolution through initialized CodeCairn state;
- the Pico Source Journal and its local recovery protocol;
- the Pico source importer and Agent Trace normalization;
- import replay and CodeCairn cursor handling;
- mapping Recall Context into one Pico memory result;
- CodeCairn-side tests, packaging, and handoff metadata.

Pico owns:

- selecting `memory.backend = "codecairn"` as its default profile;
- removing the bundled EverOS backend and remembered-skill coupling;
- keeping Local Skills as a Pico capability;
- configuration, onboarding, diagnostics, and distribution;
- the runtime behavior that surfaces backend failure;
- continuity tests and the PicoBench memory-off/on campaign.

The dependency direction is:

```text
Pico plugin discovery
  -> CodeCairn Pico Memory Adapter
     -> CodeCairn service Interface
        -> CodeCairn memory domain
```

No CodeCairn memory, service, importer, or storage module depends on Pico. The
installed Integration Module implements the backend structurally, but lazily
imports Pico's public `Memory` carrier when it creates recall results because
Pico's public contract requires concrete `Memory` instances. Importing
CodeCairn core, the entry-point package, or CodeCairn without Pico installed
must still succeed. Pico may and, when CodeCairn is its fresh-install default,
must declare and pin a resolvable compatible CodeCairn distribution. Before a
registry release exists, the accepted pre-release install identity is an exact
40-character Git revision:

```text
codecairn @ git+https://github.com/Hackerismydream/CodeCairn.git@<commit>
```

A later published version may replace that VCS pin. Pico does not import
CodeCairn storage or domain internals.

The entry-point group is owned by Pico:

```text
pico.plugins
```

CodeCairn registers one entry named `codecairn`. The loaded plugin manifest ID
is `codecairn-memory`, and its Memory Backend contribution key is `codecairn`.
Pico distribution tests must resolve the recorded install specification into
an isolated environment rather than import from either source checkout or a
local path dependency. The CodeCairn task also builds and inspects its wheel,
but the pre-release Pico handoff is resolved by immutable Git identity.

The entry-point value is the resource package
`codecairn.integrations.pico`. That package contains a cheap `__init__.py` and
`pico-plugin.toml`; it does not import Pico or initialize CodeCairn during
discovery. The manifest factory resolves lazily to
`codecairn.integrations.pico.backend:make_backend`.

## Repository binding

`memory.backend = "codecairn"` is valid only inside a Git repository that has
been initialized explicitly:

```bash
codecairn init
```

Pico startup does not run `codecairn init` silently. The adapter resolves the
repository rooted at `PluginContext.services.workspace` through CodeCairn's
maintained configuration and derives the Memory Namespace from canonical
repository identity. The adapter never substitutes its process working
directory for that service grant. Pico `user_id` and `agent_id` values never
become namespace keys.

Startup fails closed when:

- the workspace is not a supported Git repository;
- CodeCairn has not been initialized;
- configured repository identity disagrees with the current workspace;
- the retrieval profile is missing, invalid, or incompatible with its index;
- CodeCairn durable or operational state cannot be opened safely.

There is no fallback to EverOS, a global namespace, an empty fake success, or a
newly initialized root.

## Pico MemoryBackend mapping

The adapter implements the following version 0.2 behavior:

| Pico operation | CodeCairn behavior |
|---|---|
| `start` | Resolve repository identity, open configured CodeCairn state, recover staged journal work, and validate recall readiness |
| user-track `recall` | Call `CodeCairnApplication.recall` for the repository namespace |
| agent-track `recall` | Return an empty result |
| `store` | Durably append one persisted after-Turn batch, import its journal suffix, and make deterministic memory recallable |
| `store_verified_outcome` | Validate one canonical verifier-backed result, append structured terminal evidence with its stable key, and make exact retry idempotent |
| `feedback` | Deliberate no-op |
| `stop` | Complete or surface adapter-owned staged journal work and close adapter resources |
| media understanding | Unsupported by this adapter |

The Pico adapter bounds user-track recall queries to 8,192 UTF-8 bytes before
calling the strict CodeCairn service interface.

`top_k` maps to the CodeCairn recall `limit`. Invalid values are rejected
rather than silently clamped. Pico session identity is capture metadata; it
does not select a Memory Namespace.

CodeCairn application operations are synchronous in version 0.1. Every adapter
operation that may perform storage, import, index, or recall work runs through
`asyncio.to_thread` so Pico's event loop is not blocked.

### Recall result

One CodeCairn Recall Context becomes one Pico memory result. The adapter does
not split, rerank, summarize, or repack CodeCairn's compiled context.

The result content is the bounded Recall Context Markdown. Its metadata
contains at least:

- `backend = "codecairn"`;
- `repo_key`;
- `rendered_memory_ids`;
- `source_uris`;
- `freshness`;
- `source_cursor`;
- `index_cursor`;
- `retrieval_profile`.

These values come from the CodeCairn recall sidecar. The adapter does not
invent provenance or treat all ranked candidates as rendered context.
CodeCairn returns a packed context rather than one comparable ranked hit, so
the Pico `Memory.score` is `0.0` and metadata contains
`score_semantics = "compiled_context_not_ranked"`. The adapter must not derive
a synthetic relevance score.

## Pico Source Journal

Pico's Session store remains Pico's conversation truth. It is append-mostly but
may be rewritten by Pico history operations, so CodeCairn does not import the
mutable Session JSONL as its durable source.

The adapter instead owns an append-only Pico Source Journal under the
CodeCairn runtime root. Physical paths use safe hashes of repository and
session identities:

```text
<codecairn-root>/sources/pico/<namespace-hash>/<session-hash>.jsonl
```

The source schema identifier is:

```text
codecairn.pico.source.v1
```

The first record binds:

- schema identifier;
- provider `pico`;
- Pico session identity;
- CodeCairn repository identity;
- source generation;
- journal creation metadata derived by the adapter.

Each later JSONL record is one complete persisted after-Turn batch supplied to
the Memory Backend. It contains:

- an adapter-generated `batch_id`;
- a strictly increasing `batch_ordinal`;
- the ordered source events supplied for that turn;
- structured message role and exact text when present;
- structured tool call identifier, name, and arguments when present;
- structured tool result status only when Pico supplied that status;
- an optional structured terminal observation when Pico supplied it.

Each batch is bounded and canonicalized before writing. Unknown source fields
may be retained as untrusted payload, but they cannot become Evidence Facts
unless the Pico importer recognizes a closed structured field.

### Journal durability

One adapter call writes a staged canonical batch and fsyncs it before changing
the journal. Under a per-journal lock it then:

1. verifies the complete committed JSONL prefix;
2. repairs only an unterminated final fragment that has no committed cursor;
3. appends the complete staged batch;
4. fsyncs the journal;
5. imports the journal suffix through `CodeCairnApplication.import_session`
   with boundary `pico_turn_end` so the after-Turn batch closes an Episode;
6. removes the staged batch only after the import cursor commits.

Startup repeats this protocol for any staged batch. A complete final record
with the same `batch_id` and digest is reused; an incomplete final fragment is
replaced from the staged bytes; conflicting bytes fail closed.

CodeCairn import replay is idempotent for the same committed journal prefix.
The current Pico AgentLoop calls MemoryBackend `store` once after each
persisted Session save that reaches its after-Turn pipeline. A stored batch may
contain Tool failures and must not be relabeled as a successful task. Version
0.2 relies on that caller contract. If a caller independently invokes `store`
twice with the same content, the two calls are two batches because Pico does
not yet provide a stable caller batch identity. Version 0.2 must not claim
arbitrary duplicate-call idempotency.

Verified Outcome Delivery is a separate optional adapter operation. Its key is
`coding-task-outcome:<payload-sha256>` and also reserves the journal session.
The journal derives one deterministic batch identity from that key. An exact
retry re-imports the committed prefix without appending; the same identity with
different bytes, an unexpected second batch, or use of the reserved session by
ordinary `store` fails closed. The adapter authors success and file-change
Evidence Facts only from a matched `pico_done_gate` call/result pair with exit
code zero. Model summaries, provider identity, configuration, and other result
fields remain untrusted payload.

## Pico Agent Trace

The Pico importer recognizes `codecairn.pico.source.v1` and emits one
provider-independent Agent Trace with:

```text
provider = "pico"
```

The importer preserves journal record identity, raw event index, raw event
digest, session identity, call identifier, role, exact text, and recognized
structured tool fields.

Evidence discipline is unchanged:

- a user or assistant message role comes from the structured source event;
- a tool call and result pair only when their structured call identifiers
  match;
- command, exit status, file change, and verification facts exist only when
  Pico supplies recognized structured observations;
- arbitrary tool-result text is not parsed as proof of success;
- an assistant statement that tests passed is an untrusted claim;
- missing structured outcome evidence produces `unknown`, not `success`.

Provider `pico` extends the closed Source Layer provider vocabulary. It does not
change Coding Memory types, capture cardinality, Supersession, Markdown
authority, index readiness, or Recall Context semantics.

## Store and recall consistency

Pico saves its Session before calling the Memory Backend. Version 0.2 therefore
has an explicit partial-state boundary:

```text
Pico Session saved
  -> CodeCairn journal appended
  -> CodeCairn import cursor committed
  -> deterministic index ready
```

If CodeCairn storage or import fails, the adapter raises a typed failure. Pico
must not report that turn as a clean memory-backed completion. The Pico Session
may already be durable and is the source for user-visible recovery; the
CodeCairn staged batch makes adapter-local replay possible when it was created.
Pico must build the backend batch from the same Session-normalized message view
it persisted, excluding Runtime-only context tags and recovery scaffolding.

When `store` returns successfully, the journal batch and deterministic capture
are durable and the required deterministic projection is ready. Semantic
extraction may remain pending and is reported as such. A later fresh Pico
process either recalls through a cursor at least as new as the committed source
cursor or receives a typed `index_not_ready` failure. It never receives a
silently stale success.

## Installation and migration

The handoff order was:

1. merge the Pico Source Journal, importer, and Memory Adapter to CodeCairn;
2. provide an immutable install specification pinned to that exact commit,
   plus the locally built wheel digest and installed-smoke result;
3. Pico declared and pinned the install specification, selected `codecairn`,
   removed EverOS product coupling, and updated onboarding;
4. the first installed continuity and paired campaign ran against Pico
   `5318daa` and CodeCairn `a501fe2`.

Existing EverOS state is left untouched. Version 0.2 neither imports nor
deletes it automatically. Migration, if later required, needs a separate
source contract and evidence plan.

## Joint acceptance

### Installed continuity

The following must pass through installed wheels:

1. initialize CodeCairn in repository A;
2. start Pico with `memory.backend = "codecairn"`;
3. complete a turn that contains a repository fact or task experience;
4. stop Pico and start a fresh process;
5. recall the expected CodeCairn memory and complete an independent verifier;
6. prove every returned memory ID and source URI belongs to repository A.

### Isolation and failure

- repository A memory never appears in repository B;
- `memory.backend = null` makes zero CodeCairn factory, lifecycle, recall,
  store, journal, import, and index calls; entry-point discovery and cheap
  module import may still occur;
- missing init, bad configuration, stale index, and corrupt journal produce
  typed failures;
- repeated import of the same committed journal prefix creates no duplicate
  Episodes or Coding Memories;
- exact retry of one caller-keyed verified outcome appends no second batch;
- a second independent `store` call is reported honestly as a second batch;
- plugin discovery succeeds from a fresh environment with no source checkout
  on `PYTHONPATH`.

### Paired evaluation

Pico owns the final campaign:

```text
memory.backend = null
vs
memory.backend = "codecairn"
```

Each pair freezes task, repetition, model, model parameters, tool set, Context
strategy, token budget, timeout, retry policy, workspace seed, Pico commit, and
CodeCairn wheel digest. The treatment axis is only the Memory Backend.

The campaign reports:

- deterministic verifier pass rate and paired task delta;
- expected memory-ID Recall@K;
- main-agent input and total model tokens;
- end-to-end latency;
- tool calls, repeated reads, and tool failures;
- memory-induced regressions;
- cross-repository leakage.

Provider, infrastructure, timeout, task, and verifier failures remain separate
terminal classes. A completed campaign may produce a negative result. A
positive resume or product claim is allowed only when the checked-in manifest,
raw trials, aggregate, and claim gate support it. Existing CodeCairn coding
evidence and Pico EverOS experiments are historical baselines; neither is
relabeled as live Pico-CodeCairn integration evidence.

### First joint result

The first campaign is retained as a diagnostic, not erased or promoted:

- 32 of 32 trials and 16 of 16 pairs were valid;
- treatment passed 16 of 16 memory-dependent tasks; memory-off passed 0 of 16;
- expected-memory Recall@5 was 100%;
- stale and cross-repository leakage were both zero;
- every hard-negative task still received three memories.

The last row makes the positive claim ineligible: the system demonstrated
continuity, but also showed that top-k retrieval forced an answer. ADR 0060
fixes that CodeCairn behavior and expands the local retrieval gate from 100
positive queries to 100 positive plus 20 unrelated queries. A new Pico
campaign, bound to the exact current dependency pin, is required before a
positive v0.2 task-effect claim.

## Delivery order

```text
v02-001 Pico Source Journal and importer
   |
v02-002 installed Pico Memory Adapter
   |
Pico codecairn-001 and codecairn-002
   |
v02-003 joint installed evidence and paired evaluation (completed diagnostic)
   |
ADR 0060 relevance admission
   |
current-pair installed rerun
```

The executable task specifications are under [`../plan/tasks/`](../plan/tasks/).
