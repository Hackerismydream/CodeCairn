# Workspace Layout

CodeCairn is one repository with independently runnable applications around one
Memory OS package:

```text
apps/
  hub-api/       foreground loopback Adapter for Hub reads
  hub-web/       Chinese React inspection application
contracts/
  hub-read/      executable version 1 read example
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
  -> same-origin Hub route
     -> loopback Hub Read Interface
        -> CodeCairnApplication
           -> memory and storage adapters
```

The browser does not know storage paths or provider credentials. The Hub API
does not implement alternate memory behavior. The three view operations are
the external seam and the test surface.

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
