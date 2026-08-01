---
status: accepted
---

# ADR 0063: Version 0.4 Onboarding Is a Separate Consent-Bound Interface

## Context

Version 0.3 lets a person inspect one repository's Coding Memory through the
read-only Hub, but an empty Hub still requires the user to locate provider
JSONL, run manual imports, and install continuous-capture integrations outside
the product journey. The next user outcome is not memory governance. It is that
a person can carry owned coding history into the local Memory OS and keep new
work flowing into the same Memory Namespace.

Historical source discovery is privacy-sensitive. A browser must not become a
general local-filesystem reader, and a convenient import button must not scan
unrelated repositories, import content that the person did not preview, or
silently edit client settings. Codex, Claude Code, and Pico also have different
real capabilities: the first two have supported provider-native JSONL importers
and explicit hooks, while Pico has only post-integration continuous capture
through the CodeCairn-owned Source Journal.

ADR 0061 deliberately keeps the Hub Read Interface read-only and bound to one
resolved Memory Namespace. Adding import or hook mutation to one of its three
operations would weaken that contract and turn a view-shaped Interface into a
generic compatibility surface.

## Decision

Version 0.4 delivers **local onboarding** with the user outcome **People can
carry memory**. Memory governance moves to version 0.5, the resident local
runtime moves to version 0.6, and Case and Skill growth moves to version 0.7.
Versions 1.0 and 2.0 keep their existing meanings.

CodeCairn adds a separate **Hub Onboarding Interface** with exactly two
operations:

| Operation | Transport | Behavior |
|---|---|---|
| Preview | `POST /hub-onboarding/v1/preview` | Discovers only supported local sources, defaults to selecting every safely attributable `new` or `incremental` candidate, optionally plans explicit Hook installation, and returns a retention disclosure plus a consent token without writing local state |
| Apply | `POST /hub-onboarding/v1/apply` | Accepts only the opaque consent token, repeats every safety preflight, then performs the bound imports and explicitly selected Hook installations |

The Interface is composed for one server-selected repository and proposed or
resolved Memory Namespace. Browser requests cannot provide a repository path,
runtime root, provider label, source path, settings path, executable, command,
or Memory Namespace. The same foreground loopback, random-token, same-origin,
no-CORS, forwarded-authority, and `Cache-Control: no-store` protections used by
the Hub Read Interface apply to the new transport Adapter. The Hub Read
Interface itself remains unchanged.

### Preview

Preview scans only fixed, reviewed Codex and Claude Code history roots. It uses
bounded regular-file reads, rejects symbolic-link traversal, and never searches
the whole home directory. A source is selectable only when provider-native
project metadata resolves exactly to the Git common directory bound to the
foreground Host, and every repository-bearing path recorded in one session
must resolve to that same directory. The Host retains the opened common-directory
object identity for the Interface lifetime: normal HEAD movement and linked
worktrees sharing that object remain valid, while replacement at the same path
is stale. An unresolved or foreign-repository source is reported but cannot be
force-mapped in the browser.

Every discovered source receives an opaque source ID. The ID is a selection
handle, not a path or authority claim. Absolute source paths, client settings,
session tokens, provider secrets, and transcript content do not enter the
browser response.

Preview is a strict no-write operation. It does not initialize CodeCairn,
create or mutate Markdown, SQLite, LanceDB, configuration, Import Checkpoints,
Hook Receipts, or client settings. It does not call an embedding provider,
semantic model, Agent Runtime, or other network dependency. Truncation and
unsupported sources are visible rather than treated as an exhaustive scan.

Omitting `selected_source_ids`, or sending it as `null`, is the one-click
default: Preview selects every safely attributable `new` or `incremental`
candidate returned for the bound repository. An `already_imported` candidate
remains visible but is not selected by default. Sending an explicit empty array
selects no historical source. The initial Hub scan uses the default and shows
only actionable sources as preselected, but this is only a proposed plan:
Preview still performs no write, and the person must review retention and
planned writes and explicitly confirm Apply. `install_capture_for` has no
analogous default; omitted or empty means no Hook installation.

A source whose committed prefix was rewritten or truncated is invalid and
cannot be selected. A concurrent Import Ledger write is different: Preview
returns the retryable `progress_unavailable` error instead of misreporting the
source as malformed or reading a stale SQLite snapshot.

A Preview request may instead select a subset of returned opaque source IDs.
CodeCairn returns a short-lived Consent Token only when at least one historical
source or Hook plan is selected. That token binds at least:

- the target repository and Memory Namespace identity, including the opened
  Git common-directory object identity;
- the preview and Adapter contract revisions;
- every selected opaque source ID and immutable source digest;
- the exact planned write classes;
- the selected client Hook plans and pre-write settings digests;
- the retention-disclosure revision and data-egress posture; and
- the token expiry.

The Apply request contains only that token. It cannot substitute a different
selection after consent.

### Apply

Before its first write, Apply rediscovers the selected sources and revalidates
the repository mapping, source digest, client version, Hook target, settings
digest, Adapter revision, retention revision, and token expiry. Any mismatch
rejects the whole plan as stale and requires a new preview. No planned item is
written before this stale preflight completes. Repository object identity is
also checked at the application and Hook-settings write boundaries so
replacement cannot be hidden inside an Adapter.

After that preflight, each source import is an independent durable operation
through `CodeCairnApplication.import_session`, the Import Ledger, and the Write
Intent protocol. Historical snapshots use an explicit manual-finalize boundary.
Repeated application of the same valid plan is idempotent. The onboarding
implementation never writes Markdown, SQLite, or LanceDB behind the
application Interface.

Preview derives each candidate's `new`, `incremental`, or `already_imported`
state from the durable Import Ledger when it exists. Default selection includes
only `new` and `incremental`; `already_imported` stays visible and may still be
selected explicitly. Apply imports only an uncommitted suffix for an
incremental source. An explicitly selected already-imported source produces an
itemized `noop` and increments the skipped-session count; it never creates a
duplicate Memory. Reapplying the same token returns the same report. A new
default Preview after every candidate is already imported has no historical
write plan or Consent Token unless a Hook is explicitly selected.

The selected set is not presented as one filesystem transaction. If a later
source or Hook installation fails after an earlier import committed, the
result is `partial`; completed durable work remains, every item has an explicit
receipt, and retry does not duplicate it. Index readiness is reported
separately from durable import success.

Hook installation is never implied by historical import. It appears in the
preview, planned writes, consent token, and Apply report only when the person
explicitly selects continuous capture. Codex uses its supported `Stop` Hook;
Claude Code uses `SessionEnd`. The settings write must be atomic, idempotent,
read-back verified, and guarded by the settings digest previewed to the user.

Pico has no version 0.4 historical-backfill Adapter. The product reports this
as unsupported and may explain or observe continuous capture through the
installed CodeCairn Memory Backend and `codecairn.pico.source.v1` journal. It
does not scan Pico-owned Session history, invent a Pico import, or modify
Pico-owned configuration through this Interface.

### Retention, Demo, and model policy

The preview must disclose that CodeCairn retains normalized source identity,
deterministic Evidence Facts, bounded evidence snapshots, derived Coding
Memories, Import Ledger state, and any selected Hook Receipt. It does not
silently copy the complete provider-native transcript into Markdown or promise
that omitted facts can be re-extracted after the original source disappears.

Guided Demo remains a separately labeled static or disposable experience. It
cannot write to the user's Memory Namespace, appear as imported personal
history, produce an Onboarding acceptance result, or satisfy the real-source
completion signal.

Onboarding itself is deterministic and makes no LLM call. If any version 0.4
implementation, integration, or acceptance path selects a DeepSeek model, it
must fail closed unless the exact model identifier is
`deepseek-v4-flash`. Historical artifacts produced by older frozen protocols
remain historical evidence and are not rewritten.

Add a `v04-onboarding` source-budget stage over the same maintained roots as
`v03-acceptance`. Its ceilings are 18,500 physical product-core lines and
27,700 total maintained source lines. The final 200-line increase is reserved
for consent-integrity and cross-platform landing fixes found during review.
This is an additive implementation ceiling for version 0.4; it does not
rewrite the historical version 0.3 gate or the frozen version 0.1 and version
0.2 budgets.

## Consequences

- The product surface stays small: preview, consent, apply, then return to the
  existing Memories and Recall views.
- Source-specific complexity remains behind reviewed Adapters without exposing
  a dynamic plugin system, arbitrary action schema, or browser filesystem
  picker.
- Codex, Claude Code, and Pico remain provenance and integration identities,
  not three separate Memory Banks.
- A future source Adapter must define fixed discovery roots, exact repository
  mapping, retention behavior, bounds, stale detection, and real negative-path
  tests before it joins the static Adapter catalog.
- Version 0.4 does not add memory governance, a daemon, remote access, personal
  cross-project scope, Source Archive, Case, or Skill.
- Passing implementation tests establishes a version 0.4 implementation
  candidate. Formal completion still requires exact-candidate, installed,
  real-client, store-to-recall evidence under the maintained version 0.4
  acceptance contract.
