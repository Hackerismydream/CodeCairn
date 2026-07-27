---
id: v01-009
scope: package publication surface and learner documentation
status: ready
depends-on: [v01-008]
---

# Make CodeCairn installable and teachable

## Objective

Produce a deterministic MIT-licensed package that a new user can install
persistently through `uv tool install`, and make the implementation readable
through one maintained learning path.

## Paths

Primary:

- `pyproject.toml`
- `README.md`
- `LICENSE`
- add `CHANGELOG.md`
- add `SECURITY.md`
- add `CONTRIBUTING.md`
- add `CODE_OF_CONDUCT.md`
- package include/exclude configuration
- `.github/workflows/`
- `docs/INDEX.md`
- `docs/v0.1/`
- `docs/runtime/`
- `docs/release-readiness.md`

## Required changes

1. Add MIT license and complete package metadata: authors/maintainers, URLs,
   classifiers, keywords, and license expression/file.
2. Curate wheel and sdist contents with an allowlist. Exclude caches, local
   roots, private traces, generated build output, and bulk evidence unless a
   deliberate small verifier fixture is required.
3. Ensure `codecairn` and `codecairn-mcp` entrypoints and runtime assets are in
   both artifact types.
4. Build artifacts twice from separate clean checkouts and compare file
   inventories and content hashes, allowing only documented archive metadata
   variance if the build backend cannot eliminate it.
5. Test installation into an empty environment and execute:
   `codecairn --help`, `codecairn-mcp` protocol initialize, `codecairn init`,
   lifecycle smoke, and evidence verification against a selected bundle path.
6. Rewrite README around the five-minute product outcome. Mark current commands
   accurately; remove historical framework marketing.
7. Finish the learner set: architecture, one walkthrough, reading path, ADR
   index, operations, evaluation, contribution workflow.
8. Add a link/anchor checker and command smoke for maintained docs.
9. Document release security and contribution boundaries without promising
   support channels that do not exist.
10. Add a changelog entry that separates historical baseline evidence from
    version 0.1 release evidence.
11. Provide reviewable `AGENTS.md` and `CLAUDE.md` snippets for recall,
    remember, and history behavior without modifying those files automatically.
12. Document the real install -> init -> MCP add -> hook dry-run/install ->
    Codex trust -> doctor -> task -> next recall sequence and measure manual
    five-minute versus one-client ten-minute outcomes separately.

## Documentation quality gate

A fresh reader must be able to answer, using maintained docs:

- what the Memory OS owns;
- why there are five layers but four memory types;
- how one trace becomes active memory;
- how history and restore work;
- how Codex and Claude connect;
- which command proves each release claim;
- what is deferred.

No quickstart may require cloning the repository once the package smoke passes.

## Verification

```bash
make format
make check
make eval-smoke
make evidence-verify
make source-budget
uv build
```

Run the checked-in artifact-inventory and fresh-environment install scripts.
Run the documentation checker over README, `CONTEXT.md`, `docs/`, benchmark
READMEs, and contribution files.

## Exit criteria

- license and governance files exist;
- wheel/sdist contents are deterministic and minimal;
- the installed product works outside the source checkout;
- every maintained link and documented offline command passes;
- learner docs match the actual implementation;
- all checks pass and line ceilings remain green.
