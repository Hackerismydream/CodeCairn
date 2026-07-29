---
id: v02-003
scope: joint Pico-CodeCairn installed and paired evidence
status: blocked
depends-on: [v02-002, pico:codecairn-002]
---

# Bind the Pico-CodeCairn integration to installed and task evidence

## Objective

Prove that Pico stores and recalls repository memory through the installed
CodeCairn backend, remains isolated across repositories and processes, and has
a measurable memory-off/on task effect.

This task produces evidence. It does not add memory features or tune tasks
after observing results.

## Preconditions

- `v02-002` provides an exact CodeCairn wheel and handoff digest;
- Pico selects `memory.backend = "codecairn"` and has removed its EverOS
  product coupling;
- Pico Local Skills still pass independently;
- both repositories are clean and their task, verifier, and configuration
  digests are frozen;
- the provider/model, repetition count, retry policy, and spend ceiling are
  accepted before paid execution.

## Required tracks

### E0: Installed plugin and continuity

Using built wheels and no source checkout imports:

1. initialize repository A;
2. start Pico with the CodeCairn backend;
3. complete a turn that creates one expected repository memory;
4. stop Pico;
5. start a fresh process in repository A;
6. recall that memory and pass an independent deterministic verifier;
7. retain the memory IDs, source URIs, source cursor, index cursor, and plugin
   inventory.

### E1: Repository isolation and failure

Prove:

- repository A memory is absent from repository B;
- equal Pico user and session identifiers do not cross namespaces;
- `memory.backend = null` makes zero CodeCairn factory, lifecycle, recall,
  store, journal, import, and index calls; discovery may still import the
  cheap entry-point package;
- missing init, identity mismatch, malformed journal, import failure, and stale
  index remain typed failures;
- replay of the same committed journal prefix creates zero duplicate Episodes
  and memories;
- installed Local Skills behave the same with memory off and CodeCairn on.

### E2: Paired task evaluation

Run the same frozen tasks under:

```text
control:   memory.backend = null
treatment: memory.backend = "codecairn"
```

Freeze every other field:

- task and verifier;
- repetition identity;
- model, endpoint, and model parameters;
- Tool catalog and Context strategy;
- token budget;
- timeout and retry policy;
- initial workspace and repository identity;
- Pico commit;
- CodeCairn wheel digest.

Alternate or randomize pair order. Only missing or corrupt complete pairs may
be rerun, and retry policy must remain symmetric.

## Minimum campaign

The first formal campaign uses at least:

```text
8 cross-session tasks
x 2 backends
x 2 repetitions
= 32 planned trials
```

Expand the task set before making a narrow confidence-interval claim. A
repetition is not called a seed unless the provider accepts and records that
seed.

## Required artifacts

The joint manifest binds:

- both Git commits;
- CodeCairn wheel SHA-256;
- Pico wheel SHA-256;
- task, verifier, variant, and workspace-seed digests;
- model and provider identity;
- budget, timeout, retry, and repetition policy;
- all planned trial identities;
- environment and installed-package inventory.

Every trial records:

- terminal class;
- deterministic verifier result;
- expected and rendered memory IDs;
- repository identity hash;
- CodeCairn source and index cursors;
- main-agent input/output and total attributable model usage;
- tool calls, repeated reads, and failures;
- latency;
- bounded trace and artifact locations.

The reducer reports:

- run-level and task-level pass rate;
- paired task delta;
- Recall@K for expected memory IDs;
- token and latency deltas;
- memory-induced regressions;
- cross-repository leakage;
- Provider, infrastructure, timeout, task, cancelled, and inconclusive counts.

## Artifact ownership

Pico owns the raw PicoBench trials, paired reducer, summary, and CV claim gate.
CodeCairn owns its installed-adapter smoke and source/import invariants. A joint
manifest links the immutable artifacts and exact commits; neither repository
copies or relabels the other's historical evidence.

Existing CodeCairn coding benchmark results used pre-retrieved CodeCairn
context. Existing Pico memory experiments used EverOS. They may be described
as historical baselines only and do not count as this gate.

## Claim rules

Ship completion and positive claim eligibility are separate:

```json
{
  "ship_complete": true,
  "claim_eligible": false
}
```

is valid when every planned trial is accounted for but the treatment does not
improve or preserve the accepted outcome.

No task-effect number may be published without the frozen manifest, raw trial
records, aggregate inputs, and reproducible reducer. Provider or
infrastructure failure is never silently removed from the end-to-end result.

## Verification

CodeCairn:

```bash
make format
make check
```

Pico runs its maintained distribution, continuity, and PicoBench gates from a
clean checkout. The joint offline verifier must reconstruct the aggregate
without Provider access.

## Exit criteria

- installed cross-process store-recall passes;
- cross-repository leakage is zero;
- memory-off CodeCairn call count is zero;
- every planned trial has one terminal record;
- invalid pairs are reported and do not contribute to the product delta;
- the aggregate is reproducible from raw artifacts;
- both current limitations and any negative result remain visible;
- only claim-eligible values are promoted into project or resume language.
