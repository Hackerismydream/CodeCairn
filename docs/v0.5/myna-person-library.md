# Myna Person Library

Status: version 0.5 implementation candidate. This document describes the
implemented candidate contract. It is not formal installed-product acceptance,
Pico task-effect evidence, or a claim that a future Desktop product exists.

Myna is the person-first memory runtime built over CodeCairn's existing coding
memory. CodeCairn's repository records, identifiers, Markdown, lifecycle, and
resource URIs remain unchanged. Myna adds a small local library that lets the
same Person carry an explicitly selected User Preference between repositories.

## Ownership and scope

One runtime root owns one stable, randomly generated local `Person`. The same
root returns the same `person_id` when a different repository or Agent client
opens it. A different root creates a different Person. Callers cannot submit or
replace this identity.

Recall has two effective scopes:

| Scope | Contents | Selection |
|---|---|---|
| `repository` | Existing active Coding Memory for the bound repository | Existing repository lifecycle and Recall rules |
| `global` | Explicit active User Preference promotions for the Person | Immutable references selected by the user |

Myna does not infer global scope from repository history. A promotion is not a
copy and does not create a new Coding Memory. It records the source repository,
existing Memory ID, and exact revision digest. The source Markdown, Memory ID,
and `codecairn://memory/...` URI do not change.

Only one promotion is effective for a `subject_key`. Promoting the same source
again is idempotent. A competing active source fails with
`global_preference_conflict`. If the old source has been superseded, its active
successor may append an immutable replacement promotion. Missing, inactive,
foreign, changed, or otherwise invalid source references fail closed.

For Recall, Myna derives the Person, current repository, and active scopes from
the server-bound application. Each trusted client Adapter supplies one closed
requesting-client kind; the Hub Adapter always binds it to `hub`, and browser
requests cannot override it. Myna performs one candidate union, admission
decision, type-cap pass, and context compilation across the visible sources.
An active repository preference with the same `subject_key` shadows the global
promotion; the sidecar reports the shadowed promotion and the local Memory IDs
that caused it.

## Durable records

The Person Library is local Markdown truth:

```text
library/person.md
library/global-preferences/promotion_<sha256>.md
```

Each promotion is immutable and retains its source context. SQLite remains
operational state and LanceDB remains a rebuildable search index. A namespace
reset is rejected before mutation when another repository's reset target is
still referenced by global scope. Exporting or moving a Person Library requires
an explicit future portability contract; version 0.5 does not silently fold it
into a repository namespace export.

## Upgrade and Dogfood boundary

Opening this candidate with a writable runtime upgrades its SQLite operational
schema from `codecairn-v01-5` to `codecairn-v05-1`. The upgrade is intentional:
an older binary must fail closed rather than perform a namespace reset without
understanding global promotion references. It also means an older installed
CodeCairn/Pico backend cannot continue using that upgraded runtime root.

Do not run the v0.5 Hub, Doctor, Recall, or another writable v0.5 command against
the active Pico Dogfood runtime root. Finish Dogfood first, or upgrade the exact
Pico/CodeCairn pair together and retain a recoverable backup. Developing or
reviewing this source branch does not itself migrate the live runtime.

## Myna Hub contract

Myna Hub keeps the three version 1 Hub Read routes backward compatible. When a
Person Library is composed, their responses add Person, scope, source, client,
and shadowing fields. Existing callers that use only the established Hub Read
fields continue to receive the same repository records and Recall result.

Version 0.5 adds exactly one Hub governance write:

```http
POST /hub-governance/v1/preferences/promote
Content-Type: application/json
X-CodeCairn-Hub-Token: <server-issued session token>

{"memory_id":"mem_<64 lowercase hex characters>"}
```

The body is closed and accepts only `memory_id`. Query parameters are rejected.
The browser cannot provide `person_id`, repository key, runtime root, active
scopes, source path, revision digest, replacement identity, or requesting
client. The response contains the server-bound library context and an immutable
receipt with outcome `created` or `already_promoted`.

The checked example at
[`../../contracts/hub-governance/v1.example.json`](../../contracts/hub-governance/v1.example.json)
executes the closed HTTP request, response, idempotent replay, and
owner-injection rejection. Its `evidence_boundary` records the fixture's
limitations. Its `semantics` object is a declarative contract, not derived
runtime evidence. Separate service tests exercise reference preservation,
global visibility, and repository shadowing; neither artifact is formal
product acceptance.

The established Hub Read example remains
[`../../contracts/hub-read/v1.example.json`](../../contracts/hub-read/v1.example.json).

## Product boundary

This phase is Myna Core plus a minimal local Hub. It is not a Desktop process,
daemon, account system, remote sync service, task runner, terminal, diff/tools
surface, Subagent workbench, or general memory editor. The Hub can inspect
memory, explain Recall, show Person and scope, run separately consented local
Onboarding, and promote one eligible preference by reference. Agent execution
continues to belong to Pico or another client.

Passing deterministic contract and service tests establishes an implementation
candidate only. Formal acceptance still requires an exact candidate artifact
and the separately defined real-user or installed-client evidence.
