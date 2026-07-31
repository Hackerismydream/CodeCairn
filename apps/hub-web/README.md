# CodeCairn Memory Hub

This application is the Chinese inspection and consent-bound onboarding
surface for one local CodeCairn Memory Namespace. It renders real service
results and does not import example data at runtime.

The Hub has four primary views:

- **Memories** filters and pages durable Coding Memory, then inspects exact
  content, Evidence Facts, and immutable evolution history.
- **Onboarding** discovers only history owned by the current repository,
  previews retention and planned writes, and applies an opaque consent token.
- **Recall** sends a real task to CodeCairn and renders admission or abstention,
  ranked candidates, omissions, and the compiled Recall Context.
- **System** renders a sanitized point-in-time `doctor(live=False)` result and
  a separate configuration-only recall-readiness status.

An unfiltered empty namespace also links to a static **Guided Demo**. It is
explicitly example data and performs no request or write.

## Run

From the repository root:

```bash
uv sync --locked --all-packages --all-groups
npm ci
make hub-dev
```

The foreground launcher prints the local URL. It resolves the current
repository's existing CodeCairn binding, generates an ephemeral session token,
and starts both the web application and loopback adapter. Closing the command
closes both processes.

To exercise the production web bundle:

```bash
make hub-start
```

## Workspace seams

The browser depends on the TypeScript `HubClient` Interface. Its HTTP Adapter
calls a same-origin route, which adds the private session token and forwards
only these operations:

```text
GET  /hub-read/v1/memories
POST /hub-read/v1/recall
GET  /hub-read/v1/system
POST /hub-onboarding/v1/preview
POST /hub-onboarding/v1/apply
```

The Python transport handlers live in `apps/hub-api` and call the existing
Memory OS and Onboarding Interfaces rather than storage directly. Foreground
composition injects a read-only SQLite import-progress Adapter; Markdown and
LanceDB remain behind the application Interface. Executable contract examples are
[`../../contracts/hub-read/v1.example.json`](../../contracts/hub-read/v1.example.json)
and
[`../../contracts/hub-onboarding/v1.example.json`](../../contracts/hub-onboarding/v1.example.json).

## Product boundary

The Hub is local and foreground. All ordinary inspection routes remain
read-only. The only write seam is Onboarding Apply after repository-scoped
discovery, an exact write preview, and explicit consent; no browser route
accepts a filesystem path. It has no fixture fallback, daemon, remote access,
account, team space, event stream, or general memory mutation.

The v0.4 Hub remains source-checkout-only. The released CodeCairn wheel
does not yet bundle the Python Hub application or the private web workspace, so
the repository, `uv`, Node.js, and npm are required. Packaging the Hub is a
separate release gate; this implementation does not claim a packaged release.

Run all Hub gates from the root:

```bash
make hub-check
```
