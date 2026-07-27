---
id: v01-010
scope: version 0.1 release-candidate evidence
status: ready
depends-on: [v01-009]
---

# Bind one version 0.1 release candidate to complete evidence

## Objective

Prove the installable Memory OS, agent integrations, source budget, benchmark,
and public evidence on one clean commit. This task produces evidence and fixes
release blockers; it does not add product scope.

## Preconditions

- v01-001 through v01-009 are on `main`;
- the worktree is clean;
- full-run provider credentials and spend ceiling are explicitly supplied;
- supported Codex and Claude Code versions are installed for real hook smoke;
- the release protocol and dataset digests are frozen.

## Required execution

1. Record the candidate full SHA and environment inventory.
2. Run `make format`, `make check`, `make source-budget`, and `make eval-smoke`.
3. Build wheel/sdist from the candidate and run the fresh-environment installed
   CLI/MCP smoke.
4. Install both real client hooks with backed-up configuration, finish one
   disposable session per client, verify import receipts and later recall, then
   remove only the CodeCairn hook entries and read back.
5. Run `make eval-locomo-200` as the paid rehearsal and inspect failure/cost
   records.
6. Run `make eval-locomo-full` once under the frozen protocol. If and only if
   infrastructure IDs fail, run exact repair and preserve the base.
7. Require full LoCoMo at least 82.00%; target at least 82.60%. A lower result
   blocks release and is not hidden by the historical v3 bundle.
8. Measure local recall P95 under the frozen release workload and require at
   most four seconds.
9. Run coding memory-off/on A/B in isolated workspaces and publish the observed
   outcomes without inventing a success delta.
10. Build a new evidence bundle, check redaction, run `make evidence-verify`,
    and bind its inventory to the candidate SHA.
11. Repeat offline tests after artifact generation to prove the candidate code
    is unchanged.
12. Update README/changelog/release-readiness with generated results only.

## Artifact outputs

The release commit or a no-code evidence commit directly descended from it must
contain:

- frozen release protocol;
- diagnostic and full LoCoMo manifests/raw outcomes/aggregate;
- exact repair artifacts if used;
- coding A/B manifests/outcomes;
- recall latency inputs and aggregate;
- installed smoke report;
- source-budget report;
- package inventory and hashes;
- public evidence bundle and verifier result;
- release notes with limitations.

If evidence size policy keeps some raw artifacts outside Git, the checked-in
manifest must identify an immutable public location and digest. No headline is
published until the verifier can access its required inputs.

## Final verification

```bash
git diff --exit-code
make format
make check
make eval-smoke
make source-budget
make evidence-verify
uv build
```

Then run the clean installed-artifact smoke from the newly built wheel, not the
source checkout.

## Stop conditions

Do not tag or describe release success when:

- the full run is skipped, provider-blocked, or below threshold;
- hook smoke uses fixtures only;
- package smoke imports from the checkout;
- source count exceeds either ceiling;
- the evidence bundle belongs to another commit;
- a report regenerated a headline from incomplete outcomes;
- the worktree is dirty.

## Exit criteria

- every release threshold in `docs/v0.1/evaluation-and-release.md` passes on one
  clean candidate;
- all artifacts and limitations are reviewable;
- the tag, if created, points at that exact candidate;
- no deferred feature entered the release.
