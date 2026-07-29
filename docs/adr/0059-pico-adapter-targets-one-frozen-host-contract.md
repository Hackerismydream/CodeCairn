# 0059: Pico adapter targets one frozen host contract

Date: 2026-07-29

Status: Accepted

## Context

The installed CodeCairn adapter depends on Pico's plugin discovery, manifest,
`PluginContext`, `MemoryBackend`, and concrete `Memory` carrier contracts. A
floating Pico checkout would make package acceptance and the handoff
non-reproducible.

## Decision

Version 0.2 Delivery 2 targets only:

```text
repository: Hackerismydream/pico-harness
commit:     228a36a1720b460f8dca8f03c40a47af82fa5a2a
version:    0.1.7
wheel:      pico_harness-0.1.7-py3-none-any.whl
sha256:     3df9a4510e435f861967d64f3a7af1e99277b0cc8a447d04d4bf7ce15f15f50b
```

At that identity:

- installed plugins use entry-point group `pico.plugins`;
- an entry-point value names the resource package containing
  `pico-plugin.toml`;
- the manifest contributes `memory_backends` factories receiving
  `PluginContext`;
- `PluginContext.services.workspace` is the only workspace capability;
- `MemoryBackend` has async `start`, `stop`, `recall`, `store`, and `feedback`;
- `recall` returns concrete `pico.memory_engine.Memory` values;
- `store` receives one persisted after-Turn message slice.

The Pico wheel is a locally built compatibility artifact. Its missing bundled
TUI is irrelevant to the plugin and MemoryBackend smoke and is recorded as a
limitation rather than presented as a Pico release build.

## Consequences

Implementation, installed smoke, and final handoff use this exact commit and
wheel digest. Retargeting Pico requires a new reviewed decision and a repeated
installed acceptance. CodeCairn does not modify Pico, select Pico's default
backend, remove EverOS, or claim task-effect evidence in this delivery.
