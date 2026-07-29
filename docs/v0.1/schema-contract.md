# Version 0.1 Schema Contract

Status: accepted implementation contract.

This document is the single wire-format contract for version 0.1 durable and
operational records. Domain models, Markdown, SQLite mirrors, service DTOs,
CLI JSON, MCP schemas, rebuild, and evaluation fixtures must use these names
and rules. A code agent may refine internal Python types, but it may not invent
another public field, enum, default, identity input, or lifecycle state.

Post-v0.1 amendment: `v02-001` extends the existing schema-version-1 provider
and Episode-boundary enums with `pico` and `pico_turn_end`. No field shape,
identity input, or version 0.1 record is otherwise changed.

## Versioning and compatibility

- `schema_version` is the integer `1` for every top-level record in this
  document. Nested value objects inherit their owning record's version.
- JSON objects and Markdown frontmatter are closed. Unknown fields are errors.
- Missing required fields, explicit `null` where null is not allowed, invalid
  enum values, and values beyond a bound are typed `schema_invalid` errors.
- Readers may dispatch a future positive schema version to a future reader.
  Version 0.1 readers never guess how to down-convert it.
- A pre-v0.1 runtime root is rejected before mutation with
  `legacy_root_unsupported`; owned traces must be re-imported into a fresh root.
- Standalone Memory Verification is not a version 0.1 record or operation.
  Verification is a system-derived facet of Task Experience facts.

## Scalar and collection bounds

Bounds are measured on UTF-8 encoded bytes after Unicode normalization.

| Name | Contract |
|---|---|
| typed ID | ASCII prefix plus 64 lowercase SHA-256 hex characters; at most 80 bytes |
| `repo_key` | 1..512 bytes |
| provider/session/event ID | 1..256 bytes |
| source path | 1..4,096 bytes before path-digest projection |
| title | 1..256 bytes |
| memory content, goal, result | 1..32,768 bytes each |
| subject/workstream key | 1..512 bytes |
| category/tag | 1..64 bytes |
| reason/remediation/error detail | 1..2,048 bytes |
| command/tool/file summary | 1..4,096 bytes |
| tags | at most 32 unique values |
| evidence references/facts | at most 128 of each per memory |
| actions/blockers | at most 128 actions and 64 blockers |
| expected files in one write intent | at most 256 |
| MCP page size | default 20, maximum 100 |
| recall `limit` | default 20, maximum 100 |
| recall task | 1..8,192 bytes |
| Recall Context | default maximum 8,192 model tokens; hard maximum 32,768 |

Empty strings are never aliases for `null`. Collection defaults are empty
arrays. Public arrays preserve their documented order; arrays described as
sets are deduplicated and sorted before persistence.

## Normalization and canonical identity

Before identity generation:

1. text is Unicode NFC;
2. newlines become LF;
3. leading and trailing whitespace is removed only for machine keys and tags,
   not for user-visible memory content;
4. consecutive Unicode whitespace in text machine keys becomes one ASCII
   space and the result is lowercase;
5. repository-relative path keys use `/`, remove `.` segments, reject `..`,
   preserve component case, and have no leading or trailing `/`;
6. maps use lexicographically sorted keys;
7. set-like arrays use their documented deterministic sort;
8. integers are base-10 JSON integers; floats, NaN, and infinity are forbidden
   in durable identity payloads;
9. canonical JSON is UTF-8 with `ensure_ascii=false`, sorted object keys,
   separators `,` and `:`, and no insignificant whitespace or terminal
   newline.

The identity digest is the full lowercase SHA-256 of canonical JSON. IDs use a
record prefix followed by `_` and the complete 64-character digest:

| Record | Prefix |
|---|---|
| Task Episode | `ep` |
| Coding Memory | `mem` |
| Evidence Fact | `fact` |
| Evolution Proposal | `proposal` |
| Evolution Record | `evo` |
| Processing Job | `job` |
| Write Intent | `intent` |
| Hook Receipt | `hook` |

An existing ID with byte-identical canonical content is an idempotent retry.
An existing ID with different canonical content is `identity_conflict`; the
system never appends a collision suffix. Clock time, Markdown path, provider
attempt ID, queue lease, score, and presentation fields never enter identity.

## Source records

### `EvidenceReference`

| Field | Type | Required | Contract |
|---|---|---:|---|
| `fact_id` | typed ID | yes | Resolves in the Source Fact Registry |
| `provider` | enum | yes | `codex`, `claude`, or post-v0.1 `pico` |
| `session_id` | string | yes | Provider session identity |
| `source_generation` | positive integer | yes | Monotonic generation for the owned source |
| `event_index` | non-negative integer | yes | Normalized event index |
| `event_id` | string | yes | Stable normalized event identity |
| `source_path_sha256` | 64-char hex | yes | Digest only; not an authority by itself |
| `event_sha256` | 64-char hex | yes | Digest of the normalized event |

References are ordered by `(provider, session_id, source_generation,
event_index, fact_id)`.

### `EvidenceFact`

| Field | Type | Required | Contract |
|---|---|---:|---|
| `schema_version` | integer | yes | `1` |
| `fact_id` | typed ID | yes | Deterministic from all fields except the ID |
| `repo_key` | string | yes | Memory Namespace |
| `episode_id` | typed ID or null | yes | Null only before Episode assignment |
| `reference` | `EvidenceReference` | yes | Source locator |
| `fact_kind` | enum | yes | `message`, `command`, `command_result`, `file_change`, `tool_call`, `tool_result`, `verification` |
| `role` | enum or null | yes | `user`, `assistant`, `tool`, `system`, or null when not a message |
| `value` | string | yes | Exact or deterministic bounded observation |
| `attributes` | object | yes | Closed fact-kind-specific scalar map |
| `fact_ordinal` | non-negative integer | yes | Deterministic zero-based ordinal within one normalized event |

The system derives all fact fields from a normalized event. An LLM may select
`fact_id` values but may not author or modify a fact.

`fact_ordinal` is stored because it participates in identity and must remain
re-derivable after a Markdown or SQLite round trip. `attributes` has the
following exact shape by `fact_kind`. An optional field
must be omitted rather than encoded as `null`; no other key is accepted.

| `fact_kind` | Required attributes | Optional attributes |
|---|---|---|
| `message` | none | `actor` |
| `command` | `command` | `cwd_repo_relative` |
| `command_result` | `command_fact_id`, `outcome` | `exit_code` |
| `file_change` | `path`, `change_kind` | `destination_path` |
| `tool_call` | `tool_name`, `call_id` | none |
| `tool_result` | `tool_call_fact_id`, `outcome` | none |
| `verification` | `check_name`, `outcome` | `command_fact_id`, `tool_call_fact_id` |

`outcome` is `success`, `failure`, or `unknown`; `change_kind` is `add`,
`update`, `delete`, or `move`; `exit_code` is an integer. Paths use the
repository-relative normalization above. `message` uses the top-level `role`
and exact bounded `value`. A normalized event may yield multiple facts, each
with a deterministic zero-based fact ordinal.

Evidence Fact identity inputs are schema, repo, every `EvidenceReference`
field except its nested `fact_id`, fact kind, role, value, canonical
attributes, and the fact ordinal. `episode_id` is excluded so later Episode
assignment cannot change source identity; the assignment is an immutable
registry projection once non-null.

### Source Fact Registry

SQLite stores one immutable registry row per Evidence Fact:

```text
(repo_key, fact_id, provider, session_id, source_generation, event_index,
 event_id, role, fact_kind, event_sha256, source_path_sha256,
 canonical_fact_json)
```

`UNIQUE(repo_key, fact_id)` and
`UNIQUE(repo_key, provider, session_id, source_generation, event_index,
fact_id)` are required. Direct User Preference creation accepts
`source_fact_ids`, resolves them through this registry, and requires every
selected fact to have `role=user`. Entrypoints never interpret arbitrary raw
source references.

## `SourceOrderKey`

| Field | Type | Required | Contract |
|---|---|---:|---|
| `trusted_timestamp_ms` | non-negative integer or null | yes | Source time only when the importer marks it trustworthy |
| `provider` | enum | yes | `codex`, `claude`, or post-v0.1 `pico` |
| `session_id` | string | yes | Stable source session |
| `source_generation` | positive integer | yes | Source generation |
| `event_index` | non-negative integer | yes | Normalized event position |

Keys in one provider/session/generation are ordered by `event_index`. Keys
across sessions are comparable only when both have trusted timestamps; ties
break by provider, session, generation, and event index. If cross-session
recency cannot be proven, automatic Preference or Knowledge evolution chooses
`keep_both`. Import time and `created_at_ms` are never recency evidence.

## `TaskEpisode`

| Field | Type | Required | Contract |
|---|---|---:|---|
| `schema_version` | integer | yes | `1` |
| `episode_id` | typed ID | yes | Stable span identity |
| `repo_key` | string | yes | Memory Namespace |
| `provider` | enum | yes | `codex`, `claude`, or post-v0.1 `pico` |
| `session_id` | string | yes | Provider session |
| `source_generation` | positive integer | yes | Source generation |
| `start_event_index` | non-negative integer | yes | Inclusive |
| `end_event_index_exclusive` | positive integer | yes | Greater than start |
| `opening_event_id` | string | yes | Stable first user event |
| `boundary_kind` | enum | yes | `next_user`, `codex_stop`, `claude_session_end`, `manual_finalize`, or post-v0.1 `pico_turn_end` |
| `continues_episode_id` | typed ID or null | yes | Linked only under the continuation rule |
| `source_order_key` | object | yes | Opening-task order |
| `prefix_sha256` | 64-char hex | yes | Normalized source prefix through the end cursor |

The Episode identity contains schema, repo, provider, session, source
generation, start index, exclusive end index, and opening event ID.
`boundary_kind`, `continues_episode_id`, and digests are metadata and do not
change the identity.

## Coding Memory envelope

Every Coding Memory has exactly these common fields:

| Field | Type | Required | Default/rule |
|---|---|---:|---|
| `schema_version` | integer | yes | `1` |
| `memory_id` | typed ID | yes | Stable type-specific identity |
| `repo_key` | string | yes | Memory Namespace |
| `memory_type` | enum | yes | `task_experience`, `repository_knowledge`, `user_preference`, `work_state` |
| `title` | string | yes | Bounded display title |
| `content` | string | yes | Immutable durable interpretation |
| `category` | enum | yes | Type-local enum below |
| `tags` | array of string | no | `[]`, unique sorted tags |
| `created_at_ms` | non-negative integer | yes | System creation time; not identity |
| `episode_id` | typed ID or null | yes | Required when `origin=capture` |
| `evidence` | array of `EvidenceReference` | no | `[]`; required for capture |
| `facts` | array of `EvidenceFact` | no | `[]`; required for capture |
| `origin` | enum | yes | `capture`, `agent_asserted`, `restored` |
| `restored_from` | typed ID or null | yes | Required only for `restored` |
| `restore_predecessor_id` | typed ID or null | yes | Active lineage tip superseded by this restored revision; required only for `restored` |
| `source_order_key` | object or null | yes | Required for capture; copied for restore |
| `payload` | closed object | yes | One type-specific payload below |

`status` is not stored in the immutable Memory. It is the `active` or
`superseded` projection derived from Evolution Records.

### `TaskExperiencePayload`

| Field | Type | Required | Rule |
|---|---|---:|---|
| `goal` | string | yes | Deterministic from the opening user task |
| `outcome` | enum | yes | `success`, `failure`, `partial`, `unknown` |
| `actions` | array of object | no | Ordered action facets |
| `result` | string | yes | Deterministic bounded observed result |
| `blockers` | array of string | no | Ordered source-derived blockers |
| `verification_fact_ids` | array of typed ID | no | System-derived verification facets |

An action has only `kind`, `summary`, and sorted `fact_ids`. `kind` is
`command`, `file_change`, `tool`, `decision`, or `observation`.
`category` is `implementation`, `debugging`, `review`, `evaluation`,
`operations`, or `other`. Task Experience requires `origin=capture`, one
Episode, evidence, and facts. It cannot be created by direct `remember`,
superseded, or restored.

Identity inputs are schema, repo, episode ID, and memory type.

### `RepositoryKnowledgePayload`

| Field | Type | Required | Rule |
|---|---|---:|---|
| `subject_key` | string | yes | Normalized machine subject |
| `claim` | string | yes | One reusable repository claim |

`category` is `architecture`, `convention`, `command`, `constraint`,
`solution`, or `other`. Identity inputs are schema, repo, type, subject key,
sorted source fact IDs, and canonical payload. Direct memory has no source
fact IDs.

### `UserPreferencePayload`

| Field | Type | Required | Rule |
|---|---|---:|---|
| `subject_key` | string | yes | Repository-scoped working subject |
| `preference` | string | yes | User-expressed working preference |
| `source_fact_ids` | array of typed ID | yes | At least one user-authored Source Fact |

`category` is `workflow`, `output`, `tooling`, `style`, or `other`.
`origin=agent_asserted` still requires resolvable user-authored source facts;
source-less direct Preference is rejected. Identity inputs are schema, repo,
type, subject, sorted source fact IDs, and canonical payload.

The public product name is **Repository Working Preference**. The durable
`memory_type` remains `user_preference` to avoid a broad pre-release rename.

### `WorkStatePayload`

| Field | Type | Required | Rule |
|---|---|---:|---|
| `workstream_key` | string | yes | Resolved Workstream |
| `workstream_state` | enum | yes | `open` or `closed` |
| `goal` | string | yes | Current work goal |
| `progress` | string | yes | Current observed progress |
| `blockers` | array of string | no | Current blockers |
| `next_step` | string or null | yes | Required for `open` |
| `terminal_outcome` | string or null | yes | Required for `closed` |

`category` is `issue`, `branch`, `task`, `session`, or `other`. Identity inputs
are schema, repo, type, workstream key, sorted source fact IDs, and canonical
payload.

For every restorable type, a normal identity also includes `origin`. A
restored identity additionally includes `restored_from` and
`restore_predecessor_id`; this makes each forward-only restore revision
re-derivable and prevents it from colliding with the historical Memory whose
content it copies. Restore copies title, content, category, tags, episode,
evidence, facts, `source_order_key`, and payload from `restored_from`.

The Knowledge layer is an authority/lifecycle layer, not a claim that Work
State is static factual knowledge.

## Workstream resolution

Candidate keys are:

1. exact issue reference: `issue:<repo-key>#<number>`;
2. an existing open Workstream already associated with the normalized branch;
3. opening task Episode: `task:<episode-id>`;
4. source session only when no Episode exists: `session:<provider>:<session-id>`.

A branch never creates a Workstream by itself and cannot merge unrelated tasks.
It may only continue one unambiguous existing open Workstream. Otherwise the
issue or task key wins. CLI and MCP recall accept an optional
`workstream_key`; without one the resolver applies these rules and pins nothing
when the result is ambiguous.

## Evolution records

### `EvolutionProposal`

| Field | Type | Required | Contract |
|---|---|---:|---|
| `schema_version` | integer | yes | `1` |
| `proposal_id` | typed ID | yes | Fingerprint identity |
| `repo_key` | string | yes | Memory Namespace |
| `decision` | enum | yes | `keep_both` or `supersede` |
| `relation_kind` | enum | yes | `work_state_update`, `preference_override`, `knowledge_obsolete`, `knowledge_contradiction`, `explicit_restore` |
| `predecessor_id` | typed ID or null | yes | Required for `supersede` |
| `successor_id` | typed ID | yes | Newly durable memory |
| `supporting_fact_ids` | array of typed ID | no | Sorted Source Facts |
| `source_order_key` | object or null | yes | Required when recency is a policy input |
| `proposer` | enum | yes | `capture_model`, `agent`, `user`, `system` |
| `reason` | string | yes | Bounded explanation |

The proposal identity includes every field except `proposal_id`. A structurally
valid semantic extraction completes even when its proposal is rejected by
evolution policy.

Proposal outcome is a separate operational record:
`pending`, `applied`, `kept_both`, or `rejected`. Rejection has a typed reason
and does not turn a completed semantic extraction job into `failed`.

### `EvolutionRecord`

| Field | Type | Required | Contract |
|---|---|---:|---|
| `schema_version` | integer | yes | `1` |
| `evolution_id` | typed ID | yes | Immutable edge identity |
| `repo_key` | string | yes | Memory Namespace |
| `relation_kind` | enum | yes | Same closed enum as Proposal |
| `predecessor_id` | typed ID | yes | Active before the edge |
| `successor_id` | typed ID | yes | Active after the edge |
| `proposal_id` | typed ID or null | yes | Null only for direct operator action |
| `supporting_fact_ids` | array of typed ID | no | Sorted facts |
| `source_order_key` | object or null | yes | Source order used by policy |
| `proposer` | enum | yes | Proposal actor |
| `reason` | string | yes | Audit explanation |
| `created_at_ms` | non-negative integer | yes | System clock; not identity |

Identity inputs are schema, repo, relation kind, predecessor, and successor.
Application uses `BEGIN IMMEDIATE`, compares the expected active predecessor,
and commits the edge, status projection, and index revisions in one SQLite
transaction after durable files are ready.

Required uniqueness:

- one applied successor per `(repo_key, predecessor_id)`;
- one active Work State head per `(repo_key, workstream_key)`;
- one Evolution Record per `(repo_key, evolution_id)`.

Restore sets the new Memory's `restore_predecessor_id` to the unique active tip
in the restored memory's own lineage, and the Evolution edge uses that value
as its predecessor. It never selects every active same-subject memory or an
arbitrary latest item. Zero or multiple tips returns `ambiguous_lineage`.

## Operational records

### `ProcessingJob`

| Field | Type | Required | Contract |
|---|---|---:|---|
| `schema_version` | integer | yes | `1` |
| `job_id` | typed ID | yes | Kind plus immutable input fingerprint |
| `repo_key` | string | yes | Memory Namespace |
| `job_kind` | enum | yes | `semantic_extract` or `index_project` |
| `status` | enum | yes | `pending`, `leased`, `completed`, `failed` |
| `input_fingerprint` | 64-char hex | yes | Immutable work identity |
| `output_fingerprint` | 64-char hex or null | yes | Required when completed |
| `attempt_count` | non-negative integer | yes | Bounded by configured policy |
| `lease_owner` | string or null | yes | Required only while leased |
| `lease_expires_at_ms` | integer or null | yes | Required only while leased |
| `error_code` | string or null | yes | Required only when failed |
| `error_detail` | string or null | yes | Bounded and secret-redacted |
| `created_at_ms` | non-negative integer | yes | Operational time |
| `updated_at_ms` | non-negative integer | yes | Operational time |

Semantic provider/schema failure changes the semantic job to `failed`.
Evolution `kept_both` or `rejected` does not.

### `WriteIntent`

| Field | Type | Required | Contract |
|---|---|---:|---|
| `schema_version` | integer | yes | `1` |
| `operation_id` | typed ID | yes | Deterministic operation identity |
| `repo_key` | string | yes | Memory Namespace |
| `operation_kind` | enum | yes | `capture`, `semantic_commit`, `evolution`, `restore`, `direct_memory` |
| `status` | enum | yes | `prepared`, `completed`, `conflicted` |
| `expected_files` | array of object | yes | Relative safe path, content SHA-256, record kind and ID |
| `memory_ids` | array of typed ID | no | Sorted |
| `evolution_ids` | array of typed ID | no | Sorted |
| `prior_source_cursor` | integer or null | yes | Capture only |
| `target_source_cursor` | integer or null | yes | Capture only |
| `prepared_payload_json` | object | yes | Deterministic recovery input |
| `error_code` | string or null | yes | Required when conflicted |
| `created_at_ms` | non-negative integer | yes | Operational time |
| `completed_at_ms` | non-negative integer or null | yes | Required when completed |

No source cursor advances in the transaction that creates `prepared`.

### `HookReceipt`

| Field | Type | Required | Contract |
|---|---|---:|---|
| `schema_version` | integer | yes | `1` |
| `receipt_id` | typed ID | yes | Client event identity |
| `repo_key` | string or null | yes | Null only when namespace resolution failed |
| `client` | enum | yes | `codex` or `claude` |
| `event` | enum | yes | `stop` or `session_end` |
| `client_version` | string | yes | Detected version |
| `session_identity_sha256` | 64-char hex | yes | No raw session content |
| `source_identity_sha256` | 64-char hex or null | yes | Null when source unavailable |
| `outcome` | enum | yes | `imported`, `noop`, `failed`, `unsupported` |
| `error_code` | string or null | yes | Required on failure/unsupported |
| `retry_command` | string or null | yes | Secret-free executable remediation |
| `started_at_ms` | non-negative integer | yes | Operational time |
| `duration_ms` | non-negative integer | yes | Hook runtime |

Receipts never store transcript excerpts, prompts, secrets, or absolute source
paths.

## Markdown contract

Paths are:

```text
<root>/memory/<repo-slug>/<memory-type>/<memory-id>.md
<root>/evolution/<repo-slug>/<evolution-id>.md
```

Memory frontmatter is a strict line-oriented subset: fixed key order matching
the example, one `key: <canonical-json-value>` per line, no aliases, comments,
multiline scalars, duplicate keys, or unknown keys. Identity is derived from
the record DTO, not from Markdown byte layout. `record_kind` is the sole
storage-envelope field and is `coding_memory` or `evolution_record`; it is not
part of the service DTO or identity. Memory `content` is represented only by
the exact body, not duplicated in frontmatter. Evolution frontmatter contains
every Evolution Record field in schema-table order after `record_kind`, and
its body is the exact `reason`.

Example:

```markdown
---
schema_version: 1
record_kind: "coding_memory"
memory_id: "mem_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
repo_key: "acme/widgets"
memory_type: "repository_knowledge"
title: "Parser tests use golden files"
category: "convention"
tags: ["parser","tests"]
created_at_ms: 1785166855000
episode_id: "ep_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
origin: "capture"
restored_from: null
restore_predecessor_id: null
source_order_key: {"event_index":4,"provider":"codex","session_id":"session-1","source_generation":1,"trusted_timestamp_ms":1785166800000}
payload: {"claim":"Parser behavior is verified through tests/golden.","subject_key":"parser-tests"}
evidence: [{"event_id":"event-4","event_index":4,"event_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","fact_id":"fact_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","provider":"codex","session_id":"session-1","source_generation":1,"source_path_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]
facts: [{"attributes":{"change_kind":"update","path":"tests/golden"},"episode_id":"ep_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","fact_id":"fact_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","fact_kind":"file_change","fact_ordinal":0,"reference":{"event_id":"event-4","event_index":4,"event_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","fact_id":"fact_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","provider":"codex","session_id":"session-1","source_generation":1,"source_path_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"repo_key":"acme/widgets","role":null,"schema_version":1,"value":"tests/golden"}]
---
Parser behavior is verified through tests/golden.
```

The implementation fixture uses real computed IDs; the example above is a
shape example and intentionally does not claim that its placeholder digest
matches the displayed payload.

## SQLite constraints and indexes

The adapter may choose table names, but these logical constraints are
mandatory:

- Memory mirror: primary key `(repo_key, memory_id)`;
- Episode: unique `(repo_key, provider, session_id, source_generation,
  start_event_index, end_event_index_exclusive)` and `(repo_key, episode_id)`;
- closure ownership: unique `(repo_key, provider, session_id,
  source_generation, end_event_index_exclusive)`;
- Source Fact Registry constraints defined above;
- Evolution constraints defined above, including a partial unique index for an
  applied predecessor;
- active Work State head partial unique index;
- Processing Job: unique `(repo_key, job_kind, input_fingerprint)`;
- Write Intent: primary key `(repo_key, operation_id)`;
- Hook Receipt: global primary key `receipt_id`, index `(repo_key, receipt_id)`
  when repo is known, and unique
  `(client, event, session_identity_sha256, source_identity_sha256)`;
- index outbox: unique `(repo_key, memory_id, revision, target_status)`.

Foreign keys or equivalent application checks prevent cross-namespace
references. Rebuild must produce byte-equivalent canonical DTOs and the same
active projection from Markdown and Evolution Records.

## Service, CLI, and MCP mapping

The application facade owns one set of request/result DTOs. CLI `--format json`
and MCP structured results serialize those DTOs without renaming fields.
Human CLI text and Recall Markdown are presentation views.

- CLI accepts large text only through an explicit argument, `--file`, or
  stdin; exactly one source is allowed.
- CLI `remember` and MCP `remember` reject Task Experience.
- Direct User Preference uses `source_fact_ids`, never arbitrary
  `source_refs`.
- MCP tool input and output schemas are checked-in JSON snapshots generated
  from the service DTOs and compared in tests.
- MCP cursors are URL-safe base64 of canonical JSON containing
  `schema_version`, `repo_key`, stable sort key, and last `memory_id`; malformed
  or foreign cursors return `cursor_invalid`.
- Resource IDs are percent-decoded once, must match the typed-ID grammar, and
  never become filesystem paths.
- Cancellation stops presentation work and releases a lease; it never rolls
  back an already committed durable operation.

All errors expose `code`, one-line `message`, optional `remediation`, and a
stable `retryable` boolean. Stack traces, secrets, raw provider bodies, and
unbounded transcript content are never public error fields.
