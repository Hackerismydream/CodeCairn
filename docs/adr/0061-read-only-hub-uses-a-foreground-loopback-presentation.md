# ADR 0061: The Read-Only Hub Uses a Foreground Loopback Presentation

## Status

Accepted.

## Context

ADR 0052 removed an unpublished HTTP compatibility server. That server had no
distinct product caller and duplicated CLI behavior in anticipation of a future
network API.

Version 0.3 now has a concrete caller and user outcome: a local human must be
able to inspect Coding Memory, its provenance and evolution, run an explained
recall, and read a point-in-time system snapshot. A browser cannot safely or
correctly read Markdown Truth, SQLite operational state, or LanceDB directly.
Reusing CLI output would also make presentation parsing a second application
contract.

## Decision

CodeCairn adds a foreground, loopback-only Hub presentation with three
view-oriented read operations:

| Hub view | Method and path | Application behavior |
|---|---|---|
| Memories | `GET /hub-read/v1/memories` | Combines `list_memory_page`, `get_memory`, and `memory_history` |
| Recall | `POST /hub-read/v1/recall` | Returns the complete `RecallResult`, including admission, omissions, and compiled context |
| System | `GET /hub-read/v1/system` | Projects `doctor(live=False)` without the runtime root, plus configuration-only recall readiness |

One process is bound to one resolved Memory Namespace. Requests cannot select a
runtime root, repository path, provider credential, or arbitrary namespace.
The adapter listens only on `127.0.0.1`, requires a random per-process token,
does not enable CORS, and returns `Cache-Control: no-store`. The browser calls a
same-origin web route; the token is never embedded in page data. That route
accepts only loopback `Host` values and same-origin browser requests, and
rejects forwarded authorities so DNS rebinding cannot turn it into a
token-bearing proxy.

The Python adapter is an application under `apps/hub-api`. The React client is
an application under `apps/hub-web`. Both depend inward on the existing
`CodeCairnApplication`; neither imports a storage adapter. The checked-in
contract example lives under `contracts/hub-read`.

Read-only means that the Hub cannot create, replace, restore, archive, export,
reset, or otherwise change Coding Memory or Evolution Records. Recall retains
its existing bounded index preflight, so it may advance rebuildable operational
state without changing Markdown Truth.

The System view preserves the established `doctor(live=False)` meaning:
`configured` does not imply a live provider check. A separate
`recall_readiness` projection reports whether a selected network profile is
missing its credential and always says whether a live check occurred.

The launcher owns both foreground processes. Closing it closes the Hub. This is
not the version 0.5 daemon and does not promise background availability.

## Consequences

- ADR 0052 remains correct for generic compatibility HTTP and public network
  parity; this ADR authorizes only the concrete local Hub presentation.
- The Hub has one small Interface shaped around its three user-visible views,
  while Memory OS behavior remains in the existing application Module.
- The front end no longer imports a fixture at runtime. Missing local state is
  shown as empty or unavailable instead of silently falling back to examples.
- This slice runs from a source checkout. Existing root wheel and sdist
  artifacts still package the Memory OS only; shipping the Hub requires a
  separate distribution and installed-smoke decision.
- Remote access, accounts, teams, authentication products, event streams,
  write operations, and HTTP parity with CLI or MCP remain out of scope.
- A future governance Interface requires another decision. A daemon may replace
  the foreground Host without allowing the browser to bypass the Hub Interface.
