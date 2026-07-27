# Milestone: LoCoMo measurement correctness (protocol v24 prep)

Status: completed by Fable and merged in `main@954f728`. ADRs 0041–0042 and
the frozen V24 protocol assets own the result. No paid V24 score is claimed.

Parent plan: `everos-alignment-roadmap.md` (benchmark program, phase B0).

This milestone changes no retrieval semantics and spends no provider money.
It removes two verified measurement distortions and freezes the artifacts the
next paid runs need, so that Phase B1 tuning decisions are made on numbers
that mean what they appear to mean.

## Evidence

**E1 — the context packer fills roughly half of the window it claims to
fill.** The v3 base run's aggregate diagnostics
(`evidence/benchmark-v3/raw/locomo/sources/base/report.json`) average
7,916.01 context chars against 3,989.30 counted tokens — 1.98 chars per
counted token. The counter is `codecairn/utf8-two-byte-upper-bound-v1`
(2 UTF-8 bytes = 1 token, a deliberate upper bound). Real English chat text
tokenizes at ≈3.5–4 chars/token, so a 4,000-token budget saturates at roughly
2,000 real tokens — while the packer drops an average of 161.73 candidate
snippets and 5.06 selected parents per question. Questions whose first gold
turn ranked 8–20 were wrong at 25.6–32.5% versus 10.7% at rank 1; evidence
that survives ranking is being cut by the budget, not by retrieval.

**E2 — the ablation gate weighs categories in a way that inverts its own
verdict.** The 200-question diagnostic is stratified 50/50/50/50, but the full
set is 841 single-hop / 321 temporal / 282 multi-hop / 96 open-domain.
Recomputing the v5 ablation report
(`benchmark_results/locomo/locomo-diagnostic-200-v5-report.json`) under
natural weights flips the headline comparison: hierarchy scored −1.5 points
versus episode-only stratified but **+2.2 points natural-weighted** (it trades
open-domain for single-hop). The `hierarchy_vs_episode` gate therefore
measured the wrong quantity when it failed the v5 hierarchy arm.

**E3 — thinking was disabled on cost evidence that predates the current
retrieval stack.** The 82.60% run has `thinking: disabled`,
`reasoning_tokens: 0`. The decision came from a 20-question probe in the
pre-baseline era (65% no-thinking vs 80% thinking at commit `d9dd36e`) and was
never re-tested after the v14–v23 retrieval work. Whole-run answer+judge cost
was CNY 6.31; cost is not a reason to leave this unmeasured.

## Design

### A. Calibrated context budget (new frozen protocol v24)

Keep the upper-bound tokenizer — its guarantee (counted ≥ real) is what makes
the budget a hard cap — and raise the declared budget so the *real* context
reaching the answer model roughly doubles:

- `RecallPlannerConfig.context_max_tokens`: 4000 → 8000; `context_max_chars`
  scales with it (23,900 → 47,800).
- New frozen protocol files `benchmarks/locomo/diagnostic-200-v24.json` and
  `full-1540-v24.json`, copied from v23 with only the budget fields and
  version identity changed. v23 files stay immutable.
- The retrieval spend-gate token ceiling follows the protocol value it already
  reads, and its expectations are updated alongside.
- ADRs 0023/0026 refused a bigger budget so it could not hide packing bugs;
  that reasoning predates E1. The new ADR states the calibration argument:
  the budget was implicitly half its declared size, and the packing-failure
  detector (omitted-parent/snippet audit) remains in force.

### B. Natural-weight ablation reporting and gating

`compare-locomo` (`evaluation/locomo_ablation.py`) reports, per variant, both
the stratified accuracy and the natural-weighted accuracy computed with the
frozen category weights (841/321/282/96 for the full set). Gate deltas
(`hierarchy_vs_episode`, `hierarchy_vs_no_neighbors`) are evaluated on the
natural-weighted numbers; both weightings appear in the report JSON so the
stratified view is never lost. Comparison reports gain a
`weighting: natural-v1` field so old reports remain interpretable.

The weights are the module constant `LOCOMO_NATURAL_CATEGORY_WEIGHTS`
(282 multi-hop / 321 temporal / 96 open-domain / 841 single-hop), recorded as
`weighting.source = frozen-locomo10-category-counts-v1`. No flag is needed and
none is accepted to restore self-weighting: a stratified comparison set can
never weigh itself. `--natural-weight-question-set <path>` overrides the
constant with that question set's `category_targets` and records
`weighting.source = question-set-category-targets` plus the set's identity, for
a future dataset revision with a different category distribution.

### C. Thinking-enabled arm, frozen but not run

A protocol variant `diagnostic-200-v24-thinking.json`, identical to
`diagnostic-200-v24.json` except the answer model records
`thinking: enabled, reasoning_effort: high` (judge unchanged). This freezes
the E3 experiment so B1 can run it as a one-command paid comparison.

## Explicitly not in this milestone

No paid runs. No retrieval-pipeline changes (candidate budgets, reranker,
routing, slots stay byte-identical). No judge or answer-contract changes. The
runbook below is executed by the maintainer, deliberately.

## Paid-run runbook (Phase B1, maintainer-executed)

```bash
COMMIT="$(git rev-parse HEAD)"
# 1. Re-baseline the 200-question diagnostic at HEAD under v24 budgets:
CODECAIRN_RECALL_MODE=hierarchy-no-neighbors uv run codecairn eval run locomo \
  benchmarks/locomo/data/locomo10.json \
  --question-set benchmarks/locomo/diagnostic-200-v24.json \
  --run-id locomo-diagnostic-200-v24-baseline \
  --repository-commit "$COMMIT" --output-root benchmark_results \
  --root benchmark_results/runtime-v24-baseline --mode full \
  --model deepseek-v4-flash --judge-model deepseek-v4-flash --max-workers 10
# 2. Same command with --question-set benchmarks/locomo/diagnostic-200-v24-thinking.json
#    and --run-id locomo-diagnostic-200-v24-thinking.
# 3. Compare; a full-1540 v24 run is authorized only if the retrieval gate and
#    the 200-question promotion thresholds pass, per benchmarks/locomo/README.md.
```

The three-arm recall selection that precedes a promotion run is unchanged
except for its weighting default:

```bash
# Weighs every variant by the frozen LoCoMo scored-category counts. Passing
# --natural-weight-question-set is only for a dataset revision whose category
# distribution differs from 282/321/96/841.
uv run codecairn eval compare-locomo \
  benchmarks/locomo/diagnostic-40-v23.json \
  --episode-only-run "$EPISODE_ONLY_RUN" \
  --hierarchy-no-neighbors-run "$NO_NEIGHBORS_RUN" \
  --hierarchy-run "$HIERARCHY_RUN" \
  --output "$SELECTION_REPORT"

test "$(jq -r '.weighting.source' "$SELECTION_REPORT")" \
  = "frozen-locomo10-category-counts-v1"
```

## Acceptance criteria

1. v24 protocol files exist, are frozen (content-hash pinned like v23), and
   v23 files are byte-identical to before.
2. Planner default budget is 8000/47,800 and appears in `public_config()` and
   manifests; the spend gate reads the ceiling from the target protocol.
3. `compare-locomo` output contains stratified and natural-weighted accuracy
   per variant; gates evaluate natural weights; report schema records the
   weighting id. Unit tests cover the v5-report flip case from E2 (hierarchy
   −1.5 stratified / +2.2 natural on the frozen numbers).
4. Two new ADRs: budget calibration; natural-weight gating.
5. `make format` and `make check` green; no evaluation behavior other than
   the above changes (no drift in unrelated frozen contracts).
