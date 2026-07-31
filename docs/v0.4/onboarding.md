# Version 0.4 Local Onboarding and Acceptance

Status: accepted target product and acceptance contract. No checked-in artifact
currently proves formal version 0.4 completion.

ADR 0063 changes version 0.4 from memory governance to **People can carry
memory**. This document defines the smallest truthful product journey, the
per-client support contract, and the evidence required to distinguish an
implementation candidate from an accepted release.

## Product outcome

A person with owned Codex or Claude Code history can open CodeCairn for one
repository, preview exactly matching local sessions without a write, understand
what CodeCairn will and will not retain, consent to a fixed plan, import that
history idempotently, and inspect the first real Coding Memory. The same journey
may explicitly install supported continuous-capture Hooks.

Pico is shown honestly: CodeCairn supports continuous capture after Pico uses
the installed Memory Backend, but does not backfill pre-integration Pico Session
history.

The visible sequence is:

```text
Discover
  -> review safely preselected exact-repository sources
  -> read retention and write disclosure
  -> consent to one digest-bound plan
  -> apply idempotently
  -> inspect import and Hook receipts
  -> open the first real Memory
  -> try explained Recall
```

This is Live Onboarding. Guided Demo is isolated, disposable, and never appears
as the user's history.

## Hub Onboarding Interface

The Onboarding Module has two operations and is separate from the three-operation
Hub Read Interface:

```text
POST /hub-onboarding/v1/preview
POST /hub-onboarding/v1/apply
```

Both use closed JSON objects and return `Cache-Control: no-store`. The
foreground Host binds the repository; neither request accepts a repository,
namespace, runtime root, source path, settings path, provider, executable, or
command.

### Preview request

An empty request discovers every safely attributable source for the bound
repository and preselects each actionable `new` or `incremental` source:

```json
{}
```

A later Preview request can plan a subset and optional Hooks:

```json
{
  "selected_source_ids": ["src_opaque_example_not_a_path"],
  "install_capture_for": ["codex"]
}
```

`selected_source_ids` contains only IDs returned by the same bound Interface.
Omitting it or setting it to `null` selects all safe `new` and `incremental`
candidates; an explicit empty array selects none. `already_imported` candidates
remain visible but are not selected by default. The initial Hub scan uses the
omitted-field default, so actionable candidates appear preselected for
one-click onboarding. This is not an import and is not silent consent: the
person must still inspect the retention and planned-write disclosure and
explicitly confirm Apply.

`install_capture_for` is limited to `codex` and `claude` and defaults to no
Hooks when omitted or empty. Preview returns a consent token only when the
valid plan contains at least one historical source or Hook. The token binds the
complete source, repository, retention, Hook-settings, Adapter-revision,
egress, and expiry preflight. It is secret-bearing capability data and must not
be logged or placed in a URL. `selected_import_count` counts planned source
items. It can include an `already_imported` item only when the caller selects
that ID explicitly, and it is never a claim that new Memory will be created.

Preview is observational. A before/after filesystem and operational-state
digest must show that it did not initialize or mutate CodeCairn, create an
Import Checkpoint, write a Hook Receipt, edit client settings, call a model, or
make a network request.

### Apply request

Apply accepts only the token:

```json
{
  "consent_token": "consent_v1_example_not_a_real_token"
}
```

It first repeats every bound check. An expired token returns the typed
`consent_expired` HTTP error. Any source, repository, settings, client-version,
Adapter, retention, or egress change returns the typed `snapshot_stale` HTTP
error. Both are retryable `409` responses produced before any planned write;
they do not masquerade as an Apply report. The browser cannot alter the
approved source or Hook selection during Apply.

After the all-plan stale preflight passes, sources commit independently through
the existing Memory OS application Interface. The report distinguishes:

- durable imports created, skipped as already imported, or failed;
- Hook installation changed, already present, unsupported, or failed;
- index ready, pending, or failed; and
- complete, partial, no-op, or failed overall outcome.

A partial result is not rolled back or converted to success. Reapplying the
same token returns the same itemized report and cannot duplicate a Task
Experience or Hook handler. A report with `requires_new_preview=true` requires
a new Preview before failed items can be planned again.

Preview labels every candidate `new`, `incremental`, or `already_imported` from
the durable Import Ledger when available. The default selection includes only
`new` and `incremental`; `already_imported` is visible and unselected. Apply
imports only an uncommitted suffix for `incremental`, while an explicitly
selected `already_imported` item returns `outcome="noop"`, contributes to
`skipped_sessions`, and creates no duplicate Memory. Replaying the same token
returns the same report. When every candidate is already imported, a new
default Preview has no historical plan or Consent Token unless a Hook is
explicitly selected.

The executable example is
[`../../contracts/hub-onboarding/v1.example.json`](../../contracts/hub-onboarding/v1.example.json).

## Support matrix

Support is stated by operation, not by a broad claim that a client is
"connected":

| Client | Historical source on current product | Version 0.4 Preview and Apply | Continuous capture | Recall |
|---|---|---|---|---|
| Codex | Explicit owned JSONL path through CLI or MCP | Fixed `~/.codex/sessions/**/*.jsonl` root; only regular bounded sources whose every provider-native repository-bearing `cwd` matches the bound Git common directory | Explicit `Stop` Hook, installed only when included in the consent token | CLI or MCP |
| Claude Code | Explicit owned JSONL path through CLI or MCP | Fixed `~/.claude/projects/**/*.jsonl` root; only regular bounded sources whose every provider-native repository-bearing `cwd` matches the bound Git common directory | Explicit `SessionEnd` Hook, installed only when included in the consent token | CLI or MCP |
| Pico | No pre-integration history backfill | `unsupported`; no Pico-owned Session scan or selectable historical source | Installed CodeCairn Memory Backend writes the CodeCairn-owned after-Turn Source Journal; onboarding may report evidence or accurate setup steps but does not edit Pico configuration | Pico user-memory track |

An unresolved or foreign-repository source may be counted for transparency but
is never selectable. The browser has no force-map control. Manual import remains
the explicit expert escape hatch outside the Onboarding Interface.

## Retention disclosure

The preview uses a versioned disclosure with these semantics:

| Data class | Retained by CodeCairn | Ordinary Recall Context |
|---|---|---|
| Provider-native transcript | Remains an external owned source; not silently copied in full | Never injected in full |
| Local source locator, normalized source identity, and cursor | The absolute local locator and Import Ledger cursor stay in SQLite; portable Evidence References retain a locator digest rather than the path | No |
| Deterministic Evidence Facts | Bounded snapshots and Source Fact Registry rows | Only selected exact evidence |
| Task Experience and other Coding Memories | Immutable Markdown Truth plus operational mirror | Only active, admitted, budgeted excerpts |
| Evolution Record | Immutable Markdown Truth | Only when selected history or status requires it |
| Hook Receipt | Bounded operational state | No |
| Recall Context | Rebuilt for each task | It is the output, not retained memory |

Current portability means Coding Memory plus bounded portable proof. It does
not promise full transcript backup or complete re-extraction after an original
source is deleted. Any future encrypted Source Archive requires another ADR.

The disclosure also states the configured egress posture. Preview and source
normalization make no network or model call. Indexing may use the already
selected local or network embedding profile only after consent; durable import
success and index readiness remain separate outcomes. Semantic processing is
not part of Apply.

## Safety and failure contract

- Discovery is limited to the two compiled client roots. One Preview observes
  at most 256 JSONL candidates, 1,024 directories, and 256 MiB in total. Each
  source also retains the existing 64 MiB and 100,000 raw-event importer
  limits. Reaching a discovery bound reports `truncated=true` instead of
  claiming exhaustive discovery.
- Secure source-root traversal rejects symlinks, non-regular files, escape,
  oversized content, malformed JSONL, and unsupported provider envelopes.
- Exact repository mapping compares provider-native project metadata with the
  resolved Git common directory. Path resemblance or user selection is not
  sufficient.
- Opaque source IDs never expose a path and are valid only inside the bound,
  expiring preview.
- Apply validates every planned item before the first write. A stale plan does
  not partially begin.
- Once writing begins, per-source commits and Hook writes are independently
  reported; later failure yields `partial`.
- Hook settings retain unrelated entries and file mode, use atomic replacement,
  verify readback, and reject a digest changed since Preview.
- Full client settings, transcript bodies, absolute paths, session tokens,
  consent tokens, and provider credentials are never returned to or logged by
  the browser Adapter.
- The Onboarding Module does not call an LLM. Any version 0.4 path that selects
  DeepSeek must reject every identifier except `deepseek-v4-flash`.

## Implementation-candidate gate

An implementation may be called a **version 0.4 implementation candidate**
only when all of these are reproducible at one exact clean commit:

1. The checked-in example and both transport operations match one closed
   schema and reject extra input fields.
2. Preview-no-write tests compare complete relevant filesystem and operational
   state before and after success, empty, truncated, malformed, and error
   paths.
3. Loopback, same-origin, forwarded-authority, token secrecy, no-CORS, and
   no-store tests pass without weakening ADR 0061.
4. Codex and Claude Adapter tests cover fixed-root discovery, exact Git
   common-directory match, opaque IDs, symlink and root escape, malformed and
   oversized input, deterministic order, and bounded truncation.
5. Consent tests cover selection substitution, expiry, source append/rewrite,
   repository change, settings change, client-version change, and Adapter or
   disclosure revision change.
6. Apply tests cover real application-interface import, explicit Hook opt-in,
   full idempotence, per-item receipts, stale-before-write, and honest partial
   failure.
7. Pico is visibly unsupported for history while its existing continuous
   Memory Backend contract remains unchanged.
8. No fixture fallback, semantic call, or LLM judge can turn a failure into a
   successful product result. Any test or integration selecting DeepSeek pins
   `deepseek-v4-flash` exactly.
9. Documentation, type, architecture, Hub, installed-package, and source-budget
   gates pass for the exact candidate.

These checks prove implementation behavior. They do not by themselves prove
that a distributed product carries a real user's history.

## Formal acceptance gate

Formal version 0.4 acceptance requires a sealed artifact bound to exact
CodeCairn, Hub, client, and contract identities. It must show, without a fixture
fallback:

1. an installed local product starts against a clean, repository-bound state;
2. Preview discovers at least one real owned Codex source and one real owned
   Claude Code source from their supported layouts, rejects a foreign
   repository source, and produces no write;
3. the retained/excluded data and exact planned writes are presented before a
   human supplies the bound consent;
4. Apply imports each selected history, creates source-linked Task Experience,
   and a subsequent Hub Read shows the first real Memory and its Evidence
   References;
5. explained Recall against the same Namespace either admits the relevant new
   Memory with its reason or records a product failure; an abstention is not
   silently changed to success;
6. repeating the plan produces no duplicate Memory or Hook handler;
7. selected Codex and Claude Hooks subsequently capture one new real client
   boundary each, while an unselected Hook remains unchanged;
8. a stale source or settings mutation is rejected before writes, and an
   injected later-item failure is reported as partial with prior receipts
   intact;
9. Pico is presented as continuous-only and one exact installed Pico run proves
   post-integration capture without claiming historical backfill; and
10. the sealed result is independently verifiable and reports every failed,
    skipped, partial, and non-evaluable attempt.

If the formal flow invokes a DeepSeek-backed Agent or semantic step, the
manifest must record `deepseek-v4-flash`; any other DeepSeek identifier makes
the run ineligible. A source-checkout walkthrough, static Demo, contract
fixture, screenshot, or passing unit suite remains implementation evidence,
not formal acceptance.

No such sealed artifact is currently checked in. Until it exists, version 0.4
must be described as a target or implementation candidate, never as formally
accepted.
