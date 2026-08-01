# Version 0.4 Implementation Plan

Status: accepted execution plan. Task completion must be derived from the
current clean commit and its artifacts; this document does not mark any task or
the version as complete.

The product contract is [`onboarding.md`](onboarding.md), and the design
decision is ADR 0063. This plan orders implementation so every change lands
behind the two-operation Hub Onboarding Interface without weakening the Hub
Read Interface or storage authority.

## Non-negotiable invariants

- Preview scans only the fixed Codex and Claude Code roots, accepts no local
  path, performs no write, and returns no absolute path or transcript content.
- Apply accepts only one short-lived Consent Token and completes the full stale
  preflight before the first planned write.
- Provider traces remain untrusted input. Provenance and Evidence Facts remain
  deterministic; an LLM cannot author them.
- Historical imports use `CodeCairnApplication`; the Onboarding Module never
  writes Markdown, SQLite, or LanceDB directly.
- Hook installation is explicit, separately selected, atomic, idempotent, and
  read-back verified. Unrelated client settings survive unchanged.
- Pico remains continuous-only. No Adapter scans or invents pre-integration
  Pico Session history.
- Guided Demo is isolated from the user's Memory Namespace and cannot satisfy
  Live Onboarding acceptance.
- The Onboarding Module makes no LLM call. Any version 0.4 test, integration,
  or acceptance path that selects DeepSeek must use exactly
  `deepseek-v4-flash` or fail closed.

## Delivery order

| Slice | Deliverable | Depends on | Exit criterion |
|---|---|---|---|
| `v04-000` | Contract freeze and guardrails | ADR 0063 | Product contract, closed JSON example, support matrix, retention disclosure, model rule, source budget, and evidence boundary agree |
| `v04-001` | Fixed-source Adapter catalog | `v04-000` | Codex and Claude sources are found only under fixed roots, exact Git-common-directory matching admits them, and every unsafe or bounded path is tested |
| `v04-002` | Consent-bound Onboarding Module | `v04-001` | Preview is no-write; token binds the complete plan; Apply rejects stale plans before writes and returns idempotent item receipts plus separate index state |
| `v04-003` | Explicit continuous-capture Adapters | `v04-002` | Codex `Stop` and Claude Code `SessionEnd` settings changes are opt-in, digest guarded, atomic, read-back verified, and idempotent |
| `v04-004` | Loopback transport Adapter | `v04-002`, `v04-003` | Exactly two authenticated POST operations use closed objects, the stable error envelope, same-origin protections, and `no-store`; all Hub Read operations remain unchanged |
| `v04-005` | Chinese Hub journey | `v04-004` | A person can inspect sources, support, retention, egress, planned writes, and item results, then open the first real Memory without an arbitrary-file control |
| `v04-006` | Candidate verification and installed acceptance harness | `v04-005` | Deterministic gates establish an implementation candidate; a separately sealed real-client artifact is required for formal version acceptance |

The order reflects one deep Module with a small Interface. Codex and Claude
Code differences stay behind static Adapters; the browser receives a closed
core-owned schema rather than a dynamic plugin description.

## Required checks by slice

### Contract and architecture

- Validate [`../../contracts/hub-onboarding/v1.example.json`](../../contracts/hub-onboarding/v1.example.json)
  against both Python responses and the browser validator.
- Reject extra request fields and unsupported enum values.
- Preserve the three-operation Hub Read Interface and the established
  loopback, token, same-origin, forwarded-authority, no-CORS, and no-store
  policies.
- Enforce the additive `v04-onboarding` ceilings of 18,500 product-core and
  27,700 total maintained physical source lines without rewriting prior gates.
  The final 200-line allowance is reserved for reviewed consent-integrity and
  cross-platform landing fixes; it does not add product scope.

### Preview and source Adapters

- Cover empty roots, one and many sources, deterministic order, incremental and
  already-imported state, foreign repositories, missing repositories, malformed
  JSONL, symlinks, non-regular files, root escape, source and event limits, and
  global truncation.
- Prove omitted or `null` `selected_source_ids` preselects every safe `new` or
  `incremental` candidate, leaves `already_imported` visible but unselected,
  explicit `[]` selects none, Hooks remain unselected by default, and neither
  default performs a write before Apply.
- Compare the complete relevant filesystem and operational state before and
  after Preview on success and every negative path.
- Assert that browser-visible JSON contains no home path, source path, settings
  path, executable path, token echo outside its intended field, or transcript
  body.

### Consent and Apply

- Cover invalid selection, duplicate selection, token substitution, expiry,
  source append/rewrite, repository change, Import Ledger change, client
  version change, settings change, Adapter revision change, retention revision
  change, and egress change.
- Prove all-plan stale validation occurs before the first durable or settings
  write, including a source mutation at the import seam.
- Cover imported, no-op, partial, and failed reports; index ready, pending,
  failed, and not-requested states; same-token replay; and new-Preview retry.
- Prove an explicitly selected `already_imported` source yields an itemized
  no-op and skipped-session count, while an incremental source imports only its
  durable suffix and neither path duplicates a Memory.
- Prove durable success remains visible when later indexing or Hook installation
  fails.

### Product journey

- Exercise empty, available, unresolved, truncated, expired, partial, and
  complete states in Chinese.
- Show Codex, Claude Code, and Pico by actual operation support, not one generic
  connected badge.
- Make consent, data retention, egress, and planned settings writes visible
  before Apply.
- Return to the existing Hub Read Interface for the first Memory and explained
  Recall rather than duplicating those views.

## Evidence boundary

Passing the slices above at one exact clean commit permits the label
**version 0.4 implementation candidate**. It does not establish the user outcome
with a distributed product. Formal acceptance remains the sealed real-client
campaign in [`onboarding.md`](onboarding.md): installed local product, real
owned Codex and Claude Code sources, explicit human consent, store-to-Hub-read
and explained Recall, idempotent replay, selected future Hook capture, honest
stale and partial paths, Pico continuous capture, and offline verification.

A contract fixture, source-checkout test, screenshot, Guided Demo, or passing
suite must remain labeled as implementation evidence. No fallback or skipped
real-client step may be promoted to success.
