# Version 0.1 Onboarding and Operations

## Five-minute outcome

From an initialized Git repository, a new user can install, configure, connect,
import, and recall without inventing a repository key:

```bash
uvx codecairn init
codecairn doctor
codecairn import /path/to/owned-session.jsonl
codecairn recall "What should I know before changing the parser?"
```

The final release documentation may use `uvx` only after an installed-artifact
smoke proves the published package contains every runtime asset and entrypoint.

## Runtime root and namespace

The default runtime root is:

```text
~/.codecairn
```

A user may explicitly choose a repository-local or other root with `--root`.
CodeCairn never silently creates state relative to an arbitrary current working
directory.

`init` derives `repo_key` in this order:

1. normalized owner/repository from the selected Git remote;
2. stable absolute repository-path digest plus readable slug;
3. explicit `--repo-key`.

The command displays and stores the result. Later commands resolve the same
repository root before accepting it.

## `codecairn init`

`init` is idempotent and non-interactive by default:

```text
codecairn init
  [--root PATH]
  [--repo-key KEY]
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

`--force` may replace generated non-secret configuration only after parsing the
existing file. It never deletes memories, evolution history, queues, or indexes.

## Configuration contract

Configuration precedence is:

```text
explicit CLI option > environment > codecairn.toml > built-in default
```

Secrets are environment-only. `codecairn.toml` may name an environment
variable, but never contains a provider token.

Minimum file:

```toml
schema_version = 1
runtime_root = "/Users/example/.codecairn"
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

Changing provider, model, dimension, declared revision, or adapter version
marks the LanceDB projection stale and requires rebuild.

### Semantic capture

Semantic extraction uses a separately configured OpenAI-compatible chat model.
If none is configured:

- source import succeeds;
- deterministic Task Experience succeeds;
- optional Repository Knowledge, User Preference, Work State, and evolution
  proposals remain pending;
- `doctor` shows the pending count and configuration command.

This is a useful degraded mode, not a claim that semantic capture completed.

## Operational state

`doctor` renders one row per subsystem:

| Subsystem | Healthy condition | Remediation example |
|---|---|---|
| Config | schema valid, root and namespace resolved | rerun `codecairn init` |
| Source import | no failed receipts | `codecairn import <source>` |
| Semantic queue | no failed jobs; pending is explicit degraded state | configure provider, then `codecairn process --semantic --retry-failed` |
| Markdown | all files safe and valid | re-import owned trace or restore backup |
| SQLite | schema current and mirrors consistent | `codecairn doctor --repair` only for supported projections |
| Index queue | no failed or expired leases | `codecairn process --index --retry-failed` |
| LanceDB | fingerprint and document parity match | `codecairn index rebuild` |
| Hooks | supported client schema and no unacknowledged failure | reinstall or retry exact source |

Human text is the TTY default. `--format json` is stable for automation.
`--strict` exits non-zero for degraded or unhealthy state.

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
- no secret appears in config, logs, sidecars, hook receipts, or errors.
- provider absence and index staleness are distinct doctor states.
- a lost LanceDB directory rebuilds from durable truth.
- a failed semantic call retries without duplicating a memory.
- an old root is rejected before any mutation.
- all remediation commands are executable and covered by tests.
