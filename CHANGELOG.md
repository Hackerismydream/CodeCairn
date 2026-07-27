# Changelog

This project follows semantic versioning after the first public release.

## Unreleased

### Version 0.1 release candidate

- Ships a local, auditable five-layer memory runtime with four durable Coding
  Memory types.
- Imports Codex and Claude Code sessions incrementally, evolves memory through
  immutable supersession, and recalls active attributed context.
- Exposes the CLI, seven MCP tools plus one resource, and explicit
  Claude/Codex session-end hooks.
- Adds deterministic package, documentation, lifecycle, retrieval, scale, and
  evidence verification gates.
- Routes direct `remember` writes through the same recoverable cross-store
  Memory Commit protocol as transcript capture.
- Adds a redacted version 0.1 release-bundle builder and a pure verifier that
  binds lifecycle, recovery, retrieval, LoCoMo, Coding A/B, real-client,
  package, quality, and source-budget evidence to one implementation SHA.
- Fixes the production FastEmbed adapter to accept its native NumPy `float32`
  vectors while retaining finite-dimension validation.

The checked-in `benchmark-v3` result of 82.60% belongs to a historical commit
and protocol. It remains independently verifiable but is not version 0.1
release evidence. Candidate-bound LoCoMo, Coding A/B, installed-client, and
artifact evidence will be recorded by the final release task; missing or
provider-blocked runs are not release results.
