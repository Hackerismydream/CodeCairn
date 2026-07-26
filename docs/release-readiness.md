# Release Readiness

CodeCairn can build a wheel and source distribution, but current main is not a
formal open-source release.

## Current status

| Area | Status | Evidence or gap |
|---|---|---|
| Package metadata | Partial | Name, version, description, README, Python range, and dependencies exist |
| Wheel build | Passes | `uv build` creates a wheel with `py.typed` |
| Source distribution | Builds but is not curated | Default inclusion captures tests, docs, benchmarks, evidence, and local cache files |
| Test/type/architecture CI | Present | `make check` |
| Current public evidence CI | Missing | CI verifies historical `benchmark-v1`, not current `benchmark-v3` |
| Coverage policy | Missing | Coverage is reported; no fail-under gate |
| License | Missing | README explicitly grants no license before first tag |
| Release history | Missing | No tags, changelog, or release workflow |
| Security and contribution policy | Missing | No SECURITY, CONTRIBUTING, or code-of-conduct files |
| Artifact publication | Missing | CI builds but does not upload/sign/prove artifacts |
| Install-to-recall smoke | Fails product acceptance | Public index lifecycle is absent |

`uv build` success proves package construction only. It does not prove that the
archive has the intended contents, that users receive a complete product
lifecycle, or that the project is ready to publish.

## Packaging gaps

`pyproject.toml` currently lacks:

- license metadata;
- authors/maintainers;
- project URLs;
- classifiers and keywords;
- an explicit source-distribution include/exclude policy.

The current source distribution contains thousands of generated evidence files
and can include an untracked `.import_linter_cache`. Build output therefore
depends on workspace residue. Before release, the project must choose whether
public evidence belongs in the sdist, then use an allowlist or explicit
exclusions so a clean checkout and a dirty developer checkout produce the same
file inventory.

## Required release gate

A first tagged release must satisfy all of the following:

1. The public `import -> supported index lifecycle -> doctor healthy -> recall`
   smoke passes from an installed wheel.
2. Ordinary import capabilities are described accurately; five gate-managed
   memory types are not advertised as automatically extracted until wired.
3. The current public evidence bundle is verified in CI.
4. Package metadata, license, changelog, security policy, contribution policy,
   and code of conduct are present.
5. Wheel and sdist contents are allowlisted or otherwise deterministic.
6. Tests include the installed artifact rather than only the source checkout.
7. A coverage threshold is selected and enforced independently of the evidence
   snapshot.
8. CI uploads immutable artifacts and records checksums; signing/provenance is
   explicitly selected or explicitly deferred.
9. A clean tagged commit is bound to the release notes and artifact inventory.

## Evidence and release separation

Benchmark bundles and package releases have different lifecycles:

- evidence bundles preserve observed experiments and remain immutable;
- package releases describe installable software at a tagged commit;
- a historical bundle may remain valid after the package changes;
- a new package release must not silently reattribute historical retrieval
  numbers to a new model/provider composition.

`benchmark-v3` is the current public evidence, but it is not a release
identifier and does not make package version `0.1.0` production-ready.
