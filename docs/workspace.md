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

## Commands

```bash
uv sync --locked --all-packages --all-groups
npm ci
make hub-dev
make hub-check
make check
```

`make hub-dev` uses the current repository binding and runs both applications
in the foreground. `make hub-start` first builds and tests the production web
bundle, then runs the same pair with the production web server.
