---
status: working-note
last_reviewed: 2026-08-01
---

# Portable Coding Memory Product Thesis

## Purpose

This document records the product conversation that began with the version 0.3
Hub and became a broader question: what should CodeCairn preserve when a
developer changes coding agent, harness, model, or machine?

It is an agent-readable research and decision note. It does not replace
`CONTEXT.md`, an accepted ADR, the roadmap, or evidence about shipped behavior.
The sections marked **Working hypothesis** preserve the pre-research position;
the evidence review and current recommendations appear later in this document.

Speech input in the originating discussion sometimes rendered EverOS as
"FOS" or "Apple OS", and OpenViking as "Open Hacking". This note uses the
canonical product names while preserving the intended comparison.

## Current conclusions

These are the shortest agent-readable conclusions from the conversation and
research. They are recommendations until an accepted ADR changes a contract.

1. Keep **local-first Coding Memory OS** as the product category. Use Recall
   Context for what an Agent consumes; do not rename the whole
   product a Context Database without owning Resources and Skills.
2. Carry the four current Coding Memory types, bounded Evidence Facts, current
   Evidence References, and Evolution Records. Do not imply full transcript
   backup or replay after the original source disappears.
3. Treat the user as owner and the repository as the current Memory Namespace.
   Workstream is an applicability key inside that Namespace; personal scope is
   future. Codex, Claude Code, or Pico records provenance, not ownership.
4. Keep Skill in the roadmap, outside version 0.3. CodeCairn may later produce
   evidence-backed portable Candidates, while each Runtime owns installation,
   permissions, and execution. Pico Local Skills remain Pico-owned today.
5. Improve the v0.3 empty state with an isolated Guided Demo and truthful
   connection instructions. Do not put discovery, import, or hook mutations
   behind the current read-only Hub contract.
6. Design historical discovery, preview, consent, import reporting, and
   continuous capture as one future Onboarding use case with a per-client
   support matrix.

## Conversation record

### The user problem

A heavy coding-agent user accumulates useful experience across Claude Code,
Codex, Pico, and future harnesses. Harnesses and models change, but the user
still needs their preferences, repository knowledge, task experience, and
unfinished work. Today CodeCairn exposes a Memory Hub, but an empty Hub does
not yet explain or deliver that continuity.

The desired product is therefore not merely a memory plugin for one agent. It
is a user-owned, local-first system that can:

1. discover and import owned historical agent data;
2. continue capture through explicit client hooks or integrations;
3. turn provider-specific records into source-linked, evolving memory;
4. let a person understand what was remembered, where it came from, why it was
   recalled, and whether it is still active; and
5. let a later agent reuse the result without inheriting the earlier harness.

### Product insights already agreed

- The developer is the durable product subject. An agent or harness is a
  replaceable client and a provenance source.
- Repository identity is currently the strongest working scope for coding
  memory. It must not be confused with source identity.
- Database creation, indexes, runtime paths, and repository keys are
  infrastructure. The product should establish them without requiring the
  user to design storage.
- Historical import and continuous capture are two halves of one connection
  journey, not separate products.
- A simple product surface should be the result of strong abstractions, not
  missing lifecycle semantics.
- Local data is authoritative for the current product line. Remote sync,
  accounts, and team spaces are future directions, not current claims.

### Existing CodeCairn boundary

Accepted CodeCairn design already separates five authorities:

```text
Source -> Experience -> Knowledge -> Evolution -> Recall
```

Version 0.1 exposes four durable Coding Memory types: Task Experience,
Repository Knowledge, User Preference, and Work State. Source material and
Evidence Facts are not additional memory types, while Recall Context is a
derived task-shaped view. Version 0.6, not version 0.3, is the current roadmap
location for Case and Skill growth.

## Working product hypothesis before research

### Category and promise

CodeCairn should remain a **Coding Memory OS** as its category, while using
"portable coding context" to describe the user outcome:

> Your engineering memory follows you when the agent or model changes.

"Context" is provisionally the compiled material delivered to a client;
"Memory" is the durable, owned, evolving asset. Calling the whole product a
context knowledge base may make ingestion and retrieval easy to understand,
but may also hide ownership, evolution, conflict, and lifecycle. The competitor
review must test this distinction rather than assume it.

### Identity model

The provisional identity rule is:

> The user is the owner, the repository is the scope, and the agent is the
> source.

This implies that Claude Code, Codex, and Pico should not create isolated
memory banks. Their identities should remain visible as provenance while
recall can select across sources inside the same authorized scope.

### What CodeCairn carries

The provisional object model is:

| Object | Provisional role |
|---|---|
| User Preference | Durable memory; may later require personal and repository scopes |
| Repository Knowledge | Durable, repository-scoped reusable knowledge |
| Task Experience | Durable, evidence-linked account of one task and outcome |
| Work State | Durable but lifecycle-sensitive state for one active workstream |
| Source material | Retained locally or referenced under an explicit retention policy; not injected by default |
| Evidence Facts | Deterministic audit bridge from memory back to source events |
| Recall Context | Ephemeral, task-shaped compilation for an agent client |
| Case | Future aggregation of related Task Experiences |
| Skill | Future governed capability derived from repeated, verified patterns |

### Raw-data retention needs an explicit boundary

"Do not carry the raw material" must not mean unconditional deletion. The
working distinction is:

```text
provider-native history
  -> normalized source and deterministic evidence
  -> governed memory
  -> bounded Recall Context
```

Only the final projection should enter an ordinary agent prompt. The current
product needs enough durable evidence to audit the selected claims and correct
a bad Memory. Complete re-extraction of omitted facts still depends on the
original source remaining available; it is not part of the current portability
promise. The exact retention unit, privacy policy, and deletion behavior remain
open.

### Skill is downstream of memory

Skill belongs in the long-term ontology, but is not provisionally a version
0.3 primary object. An executable Skill carries tool, permission, environment,
and harness assumptions that ordinary memory does not. A candidate sequence to
test, rather than an accepted promotion rule, is:

```text
Task Experiences -> optional Case -> reviewed Skill candidate -> client-specific execution
```

CodeCairn might own portable lineage, applicability, and quality evidence. The
Agent Runtime remains responsible for installation and execution. Ownership of
versions and rollback is an open version 0.6 question. The competitor review
must determine whether EverOS or OpenViking makes a different boundary and why.

## Questions the competitor review must answer

### 1. Raw source and retention

- Does the product preserve original files, conversations, sessions, or event
  payloads?
- Does it keep full content, normalized content, evidence excerpts, or only a
  source pointer?
- Can a user inspect, delete, export, or reprocess the source?
- What enters retrieval context, and what remains cold?

### 2. Memory ontology and lifecycle

- Which objects are first-class: resource, context, memory, knowledge, case,
  skill, session, or profile?
- Which types are user-visible and which remain implementation concepts?
- How do update, conflict, supersession, expiry, and scope work?

### 3. Skill boundary and promotion

- Is Skill imported, authored, extracted, generated, or promoted from repeated
  experience?
- Is there a quality gate, review step, version, rollback, or execution safety
  boundary?
- Does the memory system store a Skill, execute it, or merely retrieve it for a
  separate agent runtime?

### 4. Product subject and namespace

- Is the primary noun "my memory", an agent, a project, a workspace, a
  repository, a Viking URI, or a cloud account?
- Which identity owns data, which identity scopes retrieval, and which identity
  is only provenance?
- How are personal, repository, agent, device, and team boundaries presented?

### 5. First-run understanding

- What does a new user see before real data exists?
- Do demos, sample memories, templates, guided import, or interactive examples
  teach the product without pretending that fixtures are personal data?
- What is the shortest journey from an empty screen to a trustworthy first
  recall?
- Which internal controls are intentionally hidden?

### 6. Capture and deployment

- How are historical sources connected and how is future capture sustained?
- Which integrations are automatic, explicit, or unsupported?
- Is local state authoritative, a cache of cloud state, or one peer in a sync
  system?

## Target user journey to validate

The current conversation suggests one simple visible sequence:

```text
Connect -> Carry over -> Organize -> Understand -> Reuse -> Grow
```

Its system counterpart is:

```text
historical import + continuous capture
  -> source identity and normalized evidence
  -> memory extraction and lifecycle
  -> Hub explanation and governance
  -> task-shaped recall for any authorized client
  -> outcome feedback
  -> later Case and Skill promotion
```

The review should simplify or correct this sequence. It must not add a feature
merely because a competitor exposes it.

## Research method and evidence labels

The review will prioritize current official product pages, authenticated UI
that the user has made available, official documentation, and official source
repositories. Each conclusion will be labeled as one of:

- **Observed**: visible in the current product UI or runnable official product.
- **Documented**: explicitly stated in current official documentation or code.
- **Inferred**: a product-design interpretation supported by omissions or
  relationships, not an advertised fact.
- **Unknown**: not established by the accessible evidence.

Public marketing text, documentation, source implementation, and shipped
behavior will not be treated as interchangeable evidence.

## Research findings

### Snapshot and evidence boundary

The review was completed on 2026-08-01 against these exact official repository
heads:

| Product | Repository snapshot |
|---|---|
| EverOS | [`6d62ecbd6f7e2cf96cd162d5ead14ce07a2037ab`](https://github.com/EverMind-AI/EverOS/tree/6d62ecbd6f7e2cf96cd162d5ead14ce07a2037ab) |
| EverMe | [`bd7b7b9f858a2380976ffa91e73dc6be7fc9d07b`](https://github.com/EverMind-AI/EverMe/tree/bd7b7b9f858a2380976ffa91e73dc6be7fc9d07b) |
| OpenViking | [`c4d2b27c641586e43f212bd8ce3b95ec5be67680`](https://github.com/volcengine/OpenViking/tree/c4d2b27c641586e43f212bd8ce3b95ec5be67680) |

The authenticated [EverOS Cloud Memory Bank](https://everos.evermind.ai/memory-bank)
was inspected in the user's browser. Its empty account state was useful for
reviewing navigation and onboarding, but could not establish populated detail
behavior. OpenViking's public landing page was available but its online Studio
did not load reliably during this review; Studio conclusions below are
therefore **Documented** from its official frontend source and documentation,
not represented as a successful online product run.

### EverOS: Memory OS with a deliberately narrow human surface

**Observed** in the current authenticated Memory Bank:

- A Memory Space selector is the top-level scope.
- The main views are `Overview`, `User Memories`, and `Agent Memories`.
- Overview counts only `Profiles`, `Episodes`, `Cases`, and `Skills` and asks a
  user-level question: "What skills has my agent learned?"
- User Memories requires selecting a user and then presents Episodes.
- Agent Memories separates Skills and Cases.
- Get Started offers two installation paths: "Let my agent do it" and "Use the
  SDK". It explains only two broad categories, User Memory and Agent Memory,
  instead of exposing the storage engine.
- The empty account showed no activity, skills, or selected user and did not
  place sample personal memories in the user's Memory Space.

**Documented** in the official implementation:

- Durable paths are partitioned by `app_id/project_id`, then by `users/user_id`
  and `agents/agent_id`. User Profile and Episodes are separated from Agent
  Cases and Skills. See [How Memory Works](https://github.com/EverMind-AI/EverOS/blob/6d62ecbd6f7e2cf96cd162d5ead14ce07a2037ab/docs/how-memory-works.md).
- The engine contains eight business kinds: Episode, Atomic Fact, Foresight,
  Profile, Agent Case, Agent Skill, Knowledge Document, and Knowledge Topic.
  Several are hidden directories, while the Cloud Memory Bank foregrounds
  only Profile, Episode, Case, and Skill. Internal ontology is intentionally
  richer than product navigation.
- Markdown is the human-readable memory authority; SQLite contains operational
  state and MemCell/buffer records; LanceDB is a rebuildable retrieval
  projection. This is a memory portability promise, not a claim that every
  provider-native input byte is represented in Markdown.
- Reflection consolidates Episodes, records `deprecated_by`, excludes the old
  items from ordinary use, and keeps the originals for history. See
  [Reflection](https://github.com/EverMind-AI/EverOS/blob/6d62ecbd6f7e2cf96cd162d5ead14ce07a2037ab/docs/reflection.md).
- EverOS has separate static and live demos. The static demo teaches the
  concept without writing personal data; the live demo exercises the real
  health, add, flush, and search path. See
  [EverOS Demo](https://github.com/EverMind-AI/EverOS/blob/6d62ecbd6f7e2cf96cd162d5ead14ce07a2037ab/docs/everos-demo.md).

EverOS does treat Skill as a first-class Agent asset. Its current implementation
runs a Skill-extraction attempt for each Agent Case above the quality threshold;
the extractor may add, update, retire, or emit nothing, and it does not require
several independent Cases before an attempt. The locked
`everalgo-agent-memory==0.4.0` algorithm dependency also defines failed-case
hypotheses, confidence adjustment, and maturity behavior. Maturity scoring is
disabled by default and returns a perfect value when disabled. The existence
of a `maturity_score` field must therefore not be mistaken for a strong
production promotion gate. See the pinned
[Skill clustering](https://github.com/EverMind-AI/EverOS/blob/6d62ecbd6f7e2cf96cd162d5ead14ce07a2037ab/src/everos/memory/strategies/trigger_skill_clustering.py)
and
[Skill extraction](https://github.com/EverMind-AI/EverOS/blob/6d62ecbd6f7e2cf96cd162d5ead14ce07a2037ab/src/everos/memory/strategies/extract_agent_skill.py)
implementations and the pinned
[dependency manifest](https://github.com/EverMind-AI/EverOS/blob/6d62ecbd6f7e2cf96cd162d5ead14ce07a2037ab/pyproject.toml).

EverMe adds the cross-Agent product layer that the EverOS engine alone does not
provide. Its import flow separates scan, merge, upload, and record. The CLI
labels the origin platform and derives a stable per-machine, per-user,
per-platform fingerprint for document-chain identity; final backend source
assignment is delegated to the managed service and was not independently
verified from this repository. At this snapshot, its cold-start scanners read
selected Markdown from Claude Code and OpenClaw directories rather than native
JSONL transcript history. Codex has a continuous plugin but no equivalent
cold-start scanner. Product support must be stated per integration and
operation, not as one broad "supports Codex" claim. See the
[EverMe repository](https://github.com/EverMind-AI/EverMe/tree/bd7b7b9f858a2380976ffa91e73dc6be7fc9d07b).

**Inferred** product lesson: EverOS uses Memory OS for ownership and lifecycle,
then hides MemCell, Atomic Fact, indexing, queue, and database concepts from the
ordinary human surface. Its simplicity is a presentation decision, not a
simple engine.

### OpenViking: Context as a unified delivery and navigation language

**Documented** in the official project:

- OpenViking calls itself a Context Database or Context File System and makes
  three first-class context types explicit: Resource, Memory, and Skill. A
  Resource is user-added static knowledge; Memory is dynamically learned from
  interaction; Skill is a relatively static callable capability. See
  [Context Types](https://github.com/volcengine/OpenViking/blob/c4d2b27c641586e43f212bd8ce3b95ec5be67680/docs/en/concepts/02-context-types.md).
- Context is not a fourth stored object. It is the umbrella and retrieval
  language across Resource, Memory, and Skill.
- L0 Abstract, L1 Overview, and L2 Detail allow progressive loading. For
  Resource and Session content that OpenViking has ingested, L2 keeps the
  retained content and structure while summaries accelerate navigation.
  "Original" here does not mean a byte-for-byte archive of every
  provider-native input. See
  [Context Layers](https://github.com/volcengine/OpenViking/blob/c4d2b27c641586e43f212bd8ce3b95ec5be67680/docs/en/concepts/03-context-layers.md).
- Session commit first archives `messages.jsonl`, then asynchronously produces
  summaries and memory. A commit can also record which Context and Skill were
  used. See
  [Session Management](https://github.com/volcengine/OpenViking/blob/c4d2b27c641586e43f212bd8ce3b95ec5be67680/docs/en/concepts/08-session.md).
- The User/Peer migration moved durable personal assets under User. A Peer is a
  stable interaction identity under a User, not the owner of the user's whole
  memory. See
  [User-Peer Migration](https://github.com/volcengine/OpenViking/blob/c4d2b27c641586e43f212bd8ce3b95ec5be67680/docs/en/migration/01-user-peer-model.md).
- Historical log ingestion is explicit and disabled by default. It uses stable
  session identity, checkpoints, incremental ingestion, and resume. It does not
  preserve all input equally: the default importer keeps user/assistant text
  while omitting tool input/output. See
  [Log Ingestion](https://github.com/volcengine/OpenViking/blob/c4d2b27c641586e43f212bd8ce3b95ec5be67680/docs/en/agent-integrations/09-log-ingestion.md).
- The Helper discovers local Agent clients, installs or repairs integrations,
  shows capture/recall/commit activity, and connects that activity back to
  Memory and Skills. See
  [OpenViking Helper](https://github.com/volcengine/OpenViking/blob/c4d2b27c641586e43f212bd8ce3b95ec5be67680/docs/en/agent-integrations/14-openviking-helper.md).
- A callable Skill definition under `user/skills` is distinct from Memory about
  Skill usage under `user/memories/skills`. The former describes capability;
  the latter learns when and how the capability worked. See
  [Skills API](https://github.com/volcengine/OpenViking/blob/c4d2b27c641586e43f212bd8ce3b95ec5be67680/docs/en/api/04-skills.md).
- Session Skill extraction and broader Agent Evolution are separate features
  and both are disabled by default. See the pinned
  [configuration reference](https://github.com/volcengine/OpenViking/blob/c4d2b27c641586e43f212bd8ce3b95ec5be67680/docs/en/guides/01-configuration.md).
- Web Studio is a separate client of a running OpenViking Server and combines
  resource management, retrieval, sessions, tasks, and operational diagnosis.
  It does not embed storage or indexing. See
  [Web Studio](https://github.com/volcengine/OpenViking/blob/c4d2b27c641586e43f212bd8ce3b95ec5be67680/web-studio/README.md).

**Inferred** product lesson: OpenViking's Context terminology works because the
product actually unifies external resources, dynamic memory, executable
skills, hierarchical navigation, and session context under one filesystem.
Using the same category name for CodeCairn without shipping those objects would
be fashion-driven rather than truthful.

Disabling both automatic paths by default supports a conservative rollout
interpretation, but the reviewed documentation does not state that rationale.
It also does not present a mature universal `Case -> Skill` promotion contract.

### Comparison

| Question | EverOS / EverMe | OpenViking | Shared lesson |
|---|---|---|---|
| Product category | Memory OS and cross-Agent personal memory | Context Database / Context File System | Category follows the owned domain, not the fashionable noun |
| Durable subject | User and Agent memories inside app/project spaces | Account/User with Peer as interaction identity | User and Agent ownership are distinct; client provenance must not be silently conflated with either |
| Main objects | Profile, Episode, Case, Skill | Resource, Memory, Skill | Hide internal extraction records from ordinary navigation |
| Raw material | MemCell/source evidence plus durable derived Markdown; soft deprecation of derived Episodes | L0/L1 summaries over retained L2 content; archived Session messages; log ingestion may omit tool I/O | Define the retention boundary and state which provider-native content was never retained |
| Skill | First-class derived Agent asset, but current maturity gate is weak | First-class capability; automatic extraction is off by default | Executable capability needs a stricter boundary than ordinary memory |
| First run | Cloud Getting Started plus separate CLI static/live demos | Web Studio plus a beta macOS/Windows x64 client-discovery Helper | Teach first, then prove a real import; never disguise fixtures as personal state |
| Import | EverMe scan/merge/upload/record, then plugins | Backfill/watch plus runtime integration and Helper | Preview, consent, idempotency, checkpoint, then continuous capture |
| Local/cloud | OSS local engine; EverMe/Cloud are separate layers | Clients connect to one configured local or remote Server | Neither reviewed design documents automatic local/cloud dual-master synchronization |

## CodeCairn decision synthesis

The competitor review supports the original working hypothesis, but sharpens
several boundaries. The following are product recommendations, not shipped
claims. Any contract change requires a separate ADR and implementation task.

### Decision 1: keep Memory OS as the category

CodeCairn should not rename itself a Context Database merely because Context is
current terminology. OpenViking earns that category by owning Resources,
Memory, Skills, their filesystem, and progressive loading. CodeCairn's strongest
and most differentiated authority is durable, source-linked, evolving Coding
Memory.

Recommended category:

> A local-first Coding Memory OS.

Recommended user promise:

> Change the agent or model without losing the engineering memory behind your
> work.

Recommended division of language:

| Surface | Product noun |
|---|---|
| Product category | Coding Memory OS |
| Durable user asset | Coding Memory |
| Human surface | Memory Hub |
| Agent delivery | Recall Context |
| Input lineage | Source and Evidence |
| Future learned procedure | Case and Skill |

Context is the useful output; Memory is the owned asset and lifecycle.

### Decision 2: user owns, repository scopes, client records provenance

The recommended CodeCairn identity rule is:

> The user is the owner. The repository/project is the current Memory
> Namespace. A Workstream is a finer applicability key inside that Namespace,
> while personal cross-project scope remains future. The Agent client records
> provenance and, for an executable asset, execution compatibility; it is not
> the owner.

The target local hierarchy is:

```text
Local Personal Space
├── Personal Preferences                         future cross-project scope
└── Project Spaces
    └── Repository identity
        ├── Coding Memories
        ├── Evolution History
        └── Sources
            ├── Codex
            ├── Claude Code
            └── Pico
```

Current durable provenance is narrower: provider, session ID, source
generation, event index and ID, source-path digest, and event digest. The path
digest is not locator authority. Client version, machine or installation ID, a
retrievable native locator, and executable compatibility metadata are target
fields that require an ADR and Schema migration. Source badges are filters and
provenance; they must not create separate top-level Memory Banks.

Version 0.1 currently has one repository Memory Namespace and presents User
Preference inside that scope. A Local Personal Space and cross-project
Preference scope are future contract changes, not current behavior. "Owner" is
a semantic ownership boundary, not a requirement to add login to the local
product. A version 0.x Local Personal Space can use one installation-local
identity and later link it to an account through an explicit migration.

### Decision 3: keep four current Memory types and carry portable proof

CodeCairn's product promise is not transcript backup and not a second code
repository. The competitor ontologies do not justify expanding the current
Memory enum:

| CodeCairn type | Competitor analogue | Why it remains distinct |
|---|---|---|
| Task Experience | EverOS Episode and the source material from which a Case may grow | One immutable bounded task and observed outcome |
| Repository Knowledge | Knowledge, entity, or project-related Memory | A reusable claim about this repository, not a duplicate Resource tree |
| User Preference | Profile and preference Memory | A user-authored working rule with scope and provenance |
| Work State | Current event, pending task, or active workstream state | Continuity that must be closed or superseded, not treated as timeless knowledge |

Case is an aggregation over Task Experiences, not a fifth version 0.3 Memory
type. Skill is a separate executable authority. Resource is external input such
as the repository itself. Identity, Soul, Tool, and Skill-usage records do not
need to become new top-level Coding Memory types merely because OpenViking can
configure them.

The near-term portable set should be:

| Asset | Carry as durable CodeCairn truth? | Reason |
|---|---:|---|
| Task Experience | Yes | One immutable account of a completed or bounded task |
| Repository Knowledge | Yes | Reusable project knowledge |
| User Preference | Yes | User-authored working preference |
| Work State | Yes | Current workstream continuity with lifecycle |
| Evidence Fact snapshot | Yes | Portable audit bridge for every capture-derived memory |
| Current `EvidenceReference` fields | Yes | Fact ID, provider, session, source generation, event index/ID, path digest, and event digest |
| Client version, machine/installation ID, native locator, compatibility | Not yet | Target metadata requiring an ADR and Schema migration |
| Evolution Record | Yes | Explains which revision is active and why |
| Recall Context | No | Derived per task and safe to rebuild |
| Full provider-native transcript | No, not silently | External source material under the current contract |
| Whole repository or documentation corpus | No | The repository remains the resource; CodeCairn references it |
| Case | Future | Cross-experience aggregation |
| Executable Skill | Future, separate authority | Higher-risk versioned capability, not another Memory enum value |

The exact rule is:

```text
provider-native source = external or explicitly archived input
portable proof         = bounded exact evidence plus current Evidence References
Coding Memory          = durable interpreted asset
Recall Context         = ephemeral task-shaped projection
```

This matches the current CodeCairn contract: capture-derived Markdown embeds
bounded Evidence Facts and raw-source references, while the original owned
transcript remains external and is not silently copied. A future encrypted
Source Archive may be useful for machine migration or reprocessing, but it must
be explicit, optional, retention-bound, and secret-aware. It is not required
for version 0.3 and must not be implied by "one-click import". Current
portability therefore means derived Memory plus bounded portable proof, not
complete replay or re-extraction after the original source disappears.

### Decision 4: Skill belongs in the roadmap, not in the version 0.3 promise

Skill should exist as a separate long-term authority because a Skill can carry
instructions, scripts, tools, permissions, environment assumptions, and
failure risk. "Memory about using a Skill" and "the executable Skill" are
different records.

The current growth hypothesis is deliberately weaker than a promotion rule:

```text
Task Experiences
  -> optional Case aggregation
  -> evidence-backed portable Skill Candidate
  -> client-owned installed Skill
  -> usage evidence returned to Memory
```

Any future Candidate must cite its successful and failed evidence,
prerequisites, applicability, compatibility, and unresolved conflicts. It is
not an active Skill. Installing or activating executable content requires an
explicit human decision, and the executing Runtime owns permission checks and
execution.

For Pico specifically, the accepted
[integration ADR](../adr/0057-pico-uses-codecairn-as-its-long-term-memory-backend.md)
keeps Local Skills Pico-owned; CodeCairn's Pico Agent-track recall is empty and
feedback is a no-op. A future CodeCairn portable Candidate therefore must not
be presented as an installed Pico Local Skill. Other Harnesses likewise own
their installation and execution contracts.

Version 0.6 needs an ADR before implementation. It must decide whether Case is
mandatory, what evidence threshold can create a Candidate, which component
owns immutable versions and rollback, and how client compatibility is
represented. EverOS and OpenViking prove that Skill deserves a separate
boundary; they do not provide one universal promotion algorithm for CodeCairn
to copy.

### Decision 5: give the empty Hub an honest first-value screen

An empty Memory Hub is not a neutral state. It leaves the user to infer the
product from navigation labels. The accepted
[version 0.3 Hub contract](../adr/0061-read-only-hub-uses-a-foreground-loopback-presentation.md)
is read-only and exposes only Memories, Recall, and System. Its immediate
empty-state refinement must stay inside that contract:

```text
Keep your engineering memory when the agent changes.

[Open Guided Demo]          separate, static or disposable, and clearly labeled
[See connection steps]      accurate CLI, hook, and Pico instructions only
[Refresh after capture]     return to this repository's real Memory
```

The Guided Demo must not write to the user's repository namespace. It should
teach one compact story:

1. a Codex or Claude Code task happened;
2. CodeCairn derived a source-linked Memory;
3. an older statement was superseded or kept active;
4. another Agent asked a related question; and
5. CodeCairn explained why it recalled that Memory.

The existing retry-policy acceptance scenario may inform that story, but an
acceptance fixture is not automatically a product Demo. A product Demo needs a
separate namespace or static artifact, explicit Demo labeling, and a reset or
no-write boundary.

A future **Onboarding** use case can add source discovery and import. It is not
part of the current Hub Read Interface. Its scan preview should show, before
any durable write:

- detected client: Codex, Claude Code, or Pico;
- mapped repository or unresolved project;
- session count, latest activity, and estimated size;
- exactly which content classes will and will not be retained;
- duplicate or already-imported state; and
- whether a continuous hook is available.

After confirmation, that future onboarding journey is:

```text
discover
  -> preview and select
  -> idempotent historical import
  -> show the first real Memory with source and evidence
  -> demonstrate one explained Recall
  -> offer continuous hook installation
  -> return to the Memory Hub
```

The Demo path and the real onboarding path must remain separate:

- **Guided Demo** uses an isolated, disposable example space and never appears
  as the user's own history.
- **Live Onboarding** imports or captures real owned data and proves the actual
  store-to-recall loop.

### Decision 6: import and hooks form one connection lifecycle

"One-click import" is acceptable marketing only if the product still exposes a
safe preview and consent boundary. The present support matrix, grounded in
[current Runtime Operations](../runtime/operations.md) and the
[version 0.2 Pico contract](../v0.2/README.md), is narrower than that target:

| Source | Current historical backfill | Current continuous capture | Current recall path | Missing onboarding capability |
|---|---|---|---|---|
| Codex | User supplies an owned Codex JSONL path to `codecairn import` or MCP `import_session` | Explicitly installed, reviewed `Stop` hook | MCP or CLI | No unified local discovery or preview UI |
| Claude Code | User supplies an owned Claude JSONL path to `codecairn import` or MCP `import_session` | Explicitly installed, selected-scope `SessionEnd` hook | MCP or CLI | No unified local discovery or preview UI |
| Pico | None for pre-integration Pico-owned Session history | Installed Memory Backend writes and imports the CodeCairn-owned `codecairn.pico.source.v1` after-Turn journal | Pico user-memory track | No historical scanner or preview UI |

This matrix describes current operations, not task-effect evidence and not a
generic promise to scan every client history. The target onboarding sequence
is:

```text
detect supported sources
  -> scan without writing
  -> map user and repository scopes
  -> preview content and retention
  -> confirm
  -> import idempotently with checkpoints
  -> report imported, skipped, failed, and unsupported items
  -> install an explicit continuous-capture integration
  -> show source health in the Hub
```

The Adapter, not the user, authors `provider`, client version, native session,
event index, locator, digest, and import mode. A memory may show "from Codex"
as a provenance badge, but the user should never have to type that label.

There is a current roadmap tension: the accepted version 0.3 Hub is read-only.
A separate static or disposable Demo link can fit that boundary. Local source
discovery, scan previews, import, and hook installation are not among the three
Hub Read operations, even when a scan itself does not mutate Memory. They
require a separate Onboarding use case and reviewed interface contract before
a UI button performs them.

### Delivery map

| Recommendation | Roadmap placement | Contract consequence |
|---|---|---|
| Explanatory v0.3 empty state and separate Guided Demo | v0.3 product refinement | No Memory-domain change; Demo isolation and packaging still need an explicit artifact boundary |
| Source discovery, preview, consent, import report, and hook offer | Unassigned Onboarding slice; do not silently fold into v0.3 | New service/interface ADR and per-client support contract |
| Hub mutation and lifecycle governance | v0.4 | Use the same application service as CLI and MCP |
| Source health backed by a continuously available service | v0.5 | Daemon, event stream, recovery, and stable local API |
| Case and portable Skill Candidate | v0.6 | New ontology, quality, ownership, compatibility, and rollback ADR |
| Personal cross-project scope | Unassigned before stable v1.0 semantics | Namespace and migration ADR |
| Remote accounts, sync, and teams | v2.0 | Authentication, encryption, isolation, conflict, and deletion contracts |

### Decision 7: local is the only current authority

For the current product line:

```text
CodeCairn local Markdown + Evolution Records = durable authority
SQLite                                  = operational state
LanceDB                                 = rebuildable search projection
Hub, CLI, MCP, Pico, Codex, Claude      = clients or adapters
```

Future cloud work should begin with backup or migration, then a deliberately
selected hosted authority. It should not begin with an implicit local/cloud
dual-master design. Stable IDs, source lineage, export, and immutable evolution
already preserve the option to add a remote service later without claiming it
today.

### Things not to copy

- Do not rename CodeCairn to Context OS without owning Resources and Skills as
  real first-class contracts.
- Do not turn Codex, Claude Code, and Pico into three isolated Memory Banks.
- Do not silently scan or import private history.
- Do not claim that importing history means backing up every original token.
- Do not place Mock data inside the user's real Project Space.
- Do not expose vector stores, queue workers, database files, MemCells, or
  extraction jobs as top-level product concepts.
- Do not call a generated procedure a reliable Skill merely because it has a
  confidence or maturity field.
- Do not promise transparent local/cloud two-way sync before conflict,
  encryption, deletion, and offline semantics are designed.

### Product chain after synthesis

The final user-visible chain is:

```text
Connect -> Carry over -> Understand -> Reuse -> Govern -> Grow
```

The system chain is:

```text
source discovery
  -> previewed historical import + explicit continuous capture
  -> normalized events and deterministic evidence
  -> four durable Coding Memory types
  -> evolution and explained recall
  -> human governance
  -> Case and governed Skill candidates
```

This keeps the product simple without collapsing the evidence and lifecycle
that make the Memory trustworthy.

## Open product decisions

The research narrows the next conversation but does not eliminate it:

1. **Onboarding placement:** decide whether source discovery and preview form a
   v0.3.x companion, begin v0.4, or become a separately named milestone. It
   must not be treated as already present in the Hub.
2. **Personal scope:** decide which Preferences belong above repository scope
   and how an installation-local owner migrates without introducing login into
   the local product.
3. **Source retention:** decide whether CodeCairn ever offers an optional,
   encrypted Source Archive, including redaction, expiry, deletion, export,
   and machine-migration behavior.
4. **Skill authority:** for v0.6, decide whether Case is mandatory, how a
   Candidate is qualified, and which component owns portable versions and
   rollback while Runtimes own installed executable Skills.
5. **Remote authority:** before v2.0, choose migration, backup, or hosted
   authority semantics explicitly; do not infer transparent two-way sync.
