# CodeCairn Memory Hub

This application is the Chinese read-only inspection surface for one local
CodeCairn Memory Namespace. It renders real `CodeCairnApplication` results and
does not import example data at runtime.

The Hub has three views:

- **Memories** filters and pages durable Coding Memory, then inspects exact
  content, Evidence Facts, and immutable evolution history.
- **Recall** sends a real task to CodeCairn and renders admission or abstention,
  ranked candidates, omissions, and the compiled Recall Context.
- **System** renders a sanitized point-in-time `doctor(live=False)` result and
  a separate configuration-only recall-readiness status.

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
```

The Python implementation lives in `apps/hub-api`. It composes the existing
Memory OS application Interface and never imports Markdown, SQLite, or LanceDB
adapters. The executable version 1 contract example is
[`../../contracts/hub-read/v1.example.json`](../../contracts/hub-read/v1.example.json).

## Product boundary

The Hub is local, foreground, and read-only. It has no fixture fallback,
daemon, remote access, account, team space, event stream, or memory mutation.
Recall may advance rebuildable index state through the existing bounded
preflight, but it cannot change Coding Memory or Evolution Records.

This slice is intentionally source-checkout-only. The released CodeCairn wheel
does not yet bundle the Python Hub application or the private web workspace, so
the repository, `uv`, Node.js, and npm are required. Packaging the Hub is a
separate release gate; this implementation does not claim that version 0.3 has
shipped.

Run all Hub gates from the root:

```bash
make hub-check
```
