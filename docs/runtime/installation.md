# Install and Connect One Client

This is the version 0.1 product acceptance path. It starts from a persistent
package install and never requires a source checkout. The repository being
remembered must already be a Git repository.

Version 0.1 is not yet published. Until it is, use the release-candidate wheel
path in place of `codecairn==0.1.0`.

## Path A: manual memory loop, five-minute budget

Start a timer before installation:

```bash
uv tool install codecairn==0.1.0
cd /path/to/your/repository
codecairn init
codecairn import /path/to/owned-session.jsonl --finalize
codecairn recall "What should I know before the next task?" --format markdown
codecairn doctor
```

Stop the timer after attributed recall and doctor both return. Record install,
init, import, recall, and total wall time separately. A model download is
product work and stays in the measurement; a previously populated model cache
must be disclosed.

Passing outcome:

- both installed entrypoints exist;
- init reports one derived or explicit repository key and provider posture;
- import creates at least one Task Experience;
- recall selects that memory with a `codecairn://memory/...` source;
- doctor identifies the same namespace and no hidden provider fallback.

This is the manual path. It does not prove a client hook or MCP connection.

## Path B: one client, ten-minute total budget

Start a separate timer before installation, repeat Path A's install and init,
then choose exactly one client.

### Claude Code

```bash
claude mcp add codecairn -- codecairn-mcp
codecairn hook install --claude --dry-run
codecairn hook install --claude
codecairn doctor
```

### Codex

```bash
codex mcp add codecairn -- codecairn-mcp
codecairn hook install --codex --dry-run
codecairn hook install --codex
codecairn doctor
```

For Codex, inspect the generated hook settings, reopen the repository, and
complete Codex's normal trust review. CodeCairn does not alter or bypass
client trust.

Now finish one small coding task in the selected client, close the supported
SessionEnd/Stop boundary, start a later task, and call the MCP `recall` tool.
Stop the timer when the later task receives attributed memory from the first
task.

Passing outcome:

- MCP initialize succeeds and exposes seven tools plus one resource template;
- hook dry-run and install show the same absolute handler;
- unrelated client settings survive installation;
- doctor has a successful or no-op hook receipt;
- the later MCP recall returns memory created by the earlier hook;
- repeating the boundary creates no duplicate memory.

The manual five-minute and one-client ten-minute timings are separate results.
An automated artifact smoke reports both lower-bound durations, but it does not
replace the real client/trust timing owned by the release-candidate task.

## Privacy before live use

Run `codecairn doctor` and inspect its privacy row. The FastEmbed profile keeps
embedding local but may download pinned model artifacts. DashScope sends
embedding input to the configured endpoint. Semantic extraction is a separate
profile and is disabled by default. Repository bindings never store provider
keys.

Hook source paths must point to transcripts the user owns. Runtime roots,
namespace exports, hook receipts, and benchmark artifacts may contain
sensitive source material and should not be committed.

## Rollback

The hook installer prints the exact handler command it added. Remove only that
entry from the selected client settings. MCP registration is managed by the
client's own remove command. `codecairn namespace export` creates a recoverable
snapshot before any confirmed namespace reset.
