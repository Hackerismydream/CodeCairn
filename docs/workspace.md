# Workspace Layout

CodeCairn is one repository with independently runnable applications around one
Memory OS package:

```text
apps/
  hub-api/       foreground loopback Adapter for Hub reads and narrow governance
  hub-web/       Chinese Myna Person Memory Hub
contracts/
  hub-read/      executable version 1 read example
  hub-onboarding/ executable version 1 Preview/Apply example
  hub-governance/ executable version 1 preference-promotion example
src/codecairn/   Memory OS package and supported CLI, MCP, hook, and Pico surfaces
tools/
  v03-acceptance/ frozen Hub scenario, adapters, human forms, reducer, verifier
tests/           Memory OS and cross-workspace behavior tests
```

The root Python project remains the `codecairn` distribution. Its
`src/codecairn` path is also part of the release inventory, source-budget
reports, learning material, and immutable evidence manifests, so moving it
under a cosmetic `packages/` directory would invalidate useful provenance
without creating a new seam.

The two Hub applications are real workspace members:

- uv owns `apps/hub-api` as `codecairn-hub-api` and resolves its dependency on
  the root `codecairn` package as a workspace dependency;
- npm owns `apps/hub-web` as `@codecairn/hub-web` and uses the root lockfile.

The dependency direction is:

```text
browser
  +-> same-origin Hub read route
  |  -> loopback Hub Read Interface
  |     +-> CodeCairnApplication
  |     +-> Myna Person Library application
  |        -> memory and storage adapters
  +-> exact same-origin Onboarding routes
  |  -> loopback Hub Onboarding Interface
  |     +-> fixed Codex/Claude history adapters
  |     +-> CodeCairnApplication import
  |     +-> explicit Codex/Claude Hook installer
  +-> one same-origin Governance route
     -> server-bound preference promotion by Memory ID
        -> immutable Person Library reference
```

The browser does not know storage paths or provider credentials. The Hub API
does not implement alternate memory behavior. The three Hub Read operations
remain one external seam and test surface. Version 0.4 adds a second seam with
only Preview and Apply; it does not turn the read Interface into a mutation
surface or an arbitrary local proxy. Version 0.5 adds a third, deliberately
narrow seam with one promotion route. Its closed request contains only a Memory
ID; Person, repository, scope, and source context remain server-bound.

The Onboarding Module is composed for one server-selected repository. Its
Preview scans fixed roots without writing and returns only opaque source IDs.
Apply accepts only the bound Consent Token, then uses the existing application
Interface and Hook Adapter. Browser-supplied repository, runtime, source, and
settings paths remain impossible. The maintained target and acceptance
contract is [`v0.4/onboarding.md`](v0.4/onboarding.md); the checked-in example
is [`../contracts/hub-onboarding/v1.example.json`](../contracts/hub-onboarding/v1.example.json).

The Myna Person Library keeps existing repository Memory unchanged and records
explicit global User Preference promotions as immutable references. The exact
scope, shadowing, fail-closed, and non-Desktop boundary is
[`v0.5/myna-person-library.md`](v0.5/myna-person-library.md); the checked
governance example is
[`../contracts/hub-governance/v1.example.json`](../contracts/hub-governance/v1.example.json).

The version 0.3 acceptance tool is also a uv workspace member. It depends on
the root `codecairn` package and `codecairn-hub-api`, but neither product
package depends on it. CodeCairn owns the campaign. Its Pico subprocess adapter
invokes the installed Agent Runtime; its public CLI and Hub adapters observe
product contracts; its Chinese participant form and separate reviewer form
produce immutable local evidence. It has no LLM judge and does not add a
product runtime.

`acceptance_results/` is local generated output, not a workspace package or
checked-in truth. Only a deliberately selected, sealed, digest-bound bundle
could become release evidence.

## Commands

```bash
uv sync --locked --all-packages --all-groups
npm ci
make hub-dev
make hub-check
make acceptance-v03-check
make check
```

`make hub-dev` uses the current repository binding and runs both applications
in the foreground. `make hub-start` first builds and tests the production web
bundle, then runs the same pair with the production web server.
`make acceptance-v03-check` type-checks the private acceptance package and runs
its offline tests; it does not call a provider or create a human-study result.
