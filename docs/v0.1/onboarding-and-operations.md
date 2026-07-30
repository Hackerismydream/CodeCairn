# Version 0.1 Onboarding and Operations

## Five-minute outcome

From an initialized Git repository, a new user can install, configure, connect,
import, and recall without inventing a repository key:

```bash
uv tool install codecairn==0.1.0
codecairn init --prefetch
codecairn doctor
codecairn import /path/to/owned-session.jsonl
codecairn recall "What should I know before changing the parser?"
```

The measured five-minute outcome is this offline manual import-to-recall path.
MCP registration, hook installation, Codex trust, one completed task, and
next-session recall form a separate ten-minute one-client integration outcome.
Hooks and MCP use the persistently installed console scripts; `uvx` is allowed
only for disposable inspection and is not the default installation contract.

## Runtime root and namespace

The default runtime root is:

```text
~/.codecairn
```

A user may explicitly choose a repository-local or other root with `--root`.
CodeCairn never silently creates state relative to an arbitrary current working
directory.

The repository binding file is:

```text
<git-common-dir>/codecairn.toml
```

It is shared by linked worktrees, remains outside tracked source, and freezes
the selected `repo_key` and runtime root. `--config` may select a different
explicit file for automation. A leading `~` in `runtime_root` expands to the
current user's home directory before canonicalization; `~user`, embedded
tildes, and unresolved environment-variable syntax are configuration errors.

`init` derives `repo_key` in this order:

1. explicit `--repo-key`;
2. a valid frozen value in the repository binding file;
3. normalized owner/repository from the selected Git remote;
4. a digest of the common Git directory's canonical path plus readable slug.

The command displays and stores the result. Later commands resolve the same
repository root before accepting it.

Remote selection uses the current branch upstream, then `origin`, then the sole
remote. Multiple remaining remotes are `remote_ambiguous` unless `--remote` is
explicit. HTTPS and SSH forms normalize to the same lowercase host plus
case-preserving owner/repository path with a trailing `.git` removed. A linked
worktree uses `git rev-parse --git-common-dir`, not its worktree administrative
directory. Moving a repository preserves identity when its frozen binding file
moves with the common Git directory; a path-derived uninitialized repository
receives a new identity after a move and must use `--repo-key` to retain one.

## `codecairn init`

`init` is idempotent and non-interactive by default:

```text
codecairn init
  [--root PATH]
  [--config PATH]
  [--repo-key KEY]
  [--remote NAME]
  [--retrieval-profile dashscope|fastembed]
  [--semantic-profile NAME|none]
  [--prefetch]
  [--check-provider]
  [--force]
```

It:

1. resolves the Git repository and Memory Namespace;
2. creates the runtime directories safely;
3. inspects provider capability without printing keys;
4. selects or requires an explicit retrieval profile;
5. writes `codecairn.toml` atomically;
6. initializes or validates SQLite schema;
7. optionally prefetches pinned local model artifacts;
8. prints working CLI, MCP, and hook commands;
9. runs a non-mutating doctor summary.
10. prints reviewable `AGENTS.md` and `CLAUDE.md` snippets without writing them.

`--force` may replace generated non-secret configuration only after parsing the
existing file. It never deletes memories, evolution history, queues, or indexes.

## Configuration contract

Configuration precedence is:

```text
explicit CLI option > environment > codecairn.toml > built-in default
```

Secrets are environment-only. `codecairn.toml` may name an environment
variable, but never contains a provider token.

Minimum repository binding file:

```toml
schema_version = 1
runtime_root = "~/.codecairn"
repo_key = "owner/repository"

[retrieval]
profile = "dashscope"
model = "qwen3.7-text-embedding"
dimension = 1024

[semantic]
profile = "none"
```

The real writer preserves portable path forms where practical. Unknown keys,
invalid enum values, incompatible dimensions, and duplicate tables are typed
startup errors.

Repository identity precedence is deliberately stricter than general runtime
configuration precedence: environment variables may override provider and
operational values, but may not silently change a frozen `repo_key`.

## Provider selection

Retrieval and semantic capture are separate.

### Retrieval

If `DASHSCOPE_API_KEY` is present, `init` recommends:

```text
profile = dashscope
model = qwen3.7-text-embedding
dimension = 1024
```

`init --check-provider` or `doctor --live` validates the configured
region/workspace endpoint with one real embedding request. Without that check,
diagnostics report `configured` rather than `live_verified`. Provider
reachability without a valid 1,024-dimension result is not success. A failed
live check returns the exact endpoint/model remediation and does not substitute
another model.

Without a DashScope key, `init` records the pinned FastEmbed profile as its
documented non-interactive default; this is an initialization decision, not a
runtime fallback. An explicit profile flag always wins. Hashing is test-only.

Changing provider, model, dimension, declared revision, adapter version, or
exact-child projection revision marks LanceDB stale and requires rebuild.

### Semantic capture

Semantic extraction uses a separately configured OpenAI-compatible chat model.
If none is configured:

- source import succeeds;
- deterministic Task Experience succeeds;
- optional Repository Knowledge, User Preference, Work State, and evolution
  proposals remain pending;
- `doctor` shows the pending count and configuration command.

This is a useful degraded mode, not a claim that semantic capture completed.

### Privacy posture

Local storage does not imply local-only inference. Before enabling a network
profile, `init` shows the affected content class and requires explicit
configuration. `doctor` reports:

```text
Storage: local
Embedding: local | network
Semantic extraction: disabled | network
Source content egress: none | memory text | trace excerpts
```

FastEmbed plus semantic `none` is the local-only profile. DashScope embedding
sends bounded memory text. A network semantic provider may receive bounded
source-derived trace excerpts. Secret redaction reduces credential exposure but
is not a content-privacy guarantee.

## Operational state

`doctor` renders one row per subsystem:

| Subsystem | Healthy condition | Remediation example |
|---|---|---|
| Config | schema valid, root and namespace resolved | rerun `codecairn init` |
| Source import | no failed receipts | `codecairn import <source>` |
| Semantic queue | no failed jobs; pending is explicit degraded state | configure provider, then `codecairn process --semantic --no-index` |
| Markdown | all files safe and valid | re-import owned trace or restore backup |
| SQLite | schema current and mirrors consistent | restore the latest namespace export |
| Index queue | no failed or expired leases | `codecairn index sync` |
| LanceDB | fingerprint and document parity match | `codecairn index rebuild` |
| Hooks | supported client schema and no unacknowledged failure | reinstall or retry exact source |
| Privacy | configured egress matches the displayed posture | choose local profiles or explicitly configure network providers |

Human text is the TTY default. `--format json` is stable for automation.
`--strict` exits non-zero for degraded or unhealthy state.

`process` and `index sync` automatically reclaim eligible failed or expired
jobs within their bounded attempt limits; no separate retry flag is required.

## Queue rules

Semantic and index jobs have:

- immutable source identity;
- pending, leased, completed, or failed state;
- bounded attempt count and last error code;
- lease expiry for crash recovery;
- idempotent completion fingerprint.

`process` takes bounded batches and never holds a provider call inside the
source-import transaction. A failed job does not roll back durable source or
Task Experience.

Every recall performs the deterministic index preflight in
[`agent-integration.md`](agent-integration.md). `process` remains the operator
surface for full semantic/index draining; it is not a hidden prerequisite for
hook-to-recall Task Experience freshness.

## Export, reset, and uninstall

`codecairn namespace export --output PATH` writes authoritative Memory and
Evolution Markdown, a manifest, and a consistent SQLite backup; LanceDB is
excluded because it is rebuildable. `codecairn namespace reset --dry-run`
lists exact namespace paths and counts. Reset requires
`--confirm <repo-key>`, first creates the export, and moves the namespace to a
timestamped backup directory.

Uninstalling the executable does not remove data. Documentation lists the
persistent tool uninstall command, repository binding file, runtime namespace,
and recoverable backup directory separately.

## Pre-v0.1 roots

The runtime detects the historical six-type schema before writing. It stops
with:

1. the detected schema version;
2. a backup instruction;
3. a fresh-root path;
4. the re-import command for owned traces.

Version 0.1 intentionally has no permanent compatibility or dual-write layer.
Frozen evidence bundles use their own verifier and remain readable.

## Operations acceptance

- `init` is idempotent in a clean Git repository.
- namespace derivation is stable across subdirectories and worktrees.
- remote selection, SSH/HTTPS normalization, repository moves, and frozen
  identity precedence are covered.
- no secret appears in config, logs, sidecars, hook receipts, or errors.
- provider absence and index staleness are distinct doctor states.
- a lost LanceDB directory rebuilds from durable truth.
- a failed semantic call retries without duplicating a memory.
- an old root is rejected before any mutation.
- all remediation commands are executable and covered by tests.
- manual import-to-recall is measured separately from one-client MCP/hook
  integration.
- export/reset dry-run and recovery preserve unrelated repository namespaces.
