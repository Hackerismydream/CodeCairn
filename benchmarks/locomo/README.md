# LoCoMo Evaluation Runbook

This is the current operational entry point for CodeCairn's V23 LoCoMo
evaluation. Historical V19-V23 design notes and superseded commands are retained
in [`HISTORICAL.md`](HISTORICAL.md).

LoCoMo remains an external CC BY-NC 4.0 input and is not redistributed in this
repository. The checked-in question-set files contain identities and protocol
contracts, not question or answer text.

## Published result and its boundary

The current public bundle, `evidence/benchmark-v3`, verifies a formal exact
repair composite:

| Field | Published value |
|---|---:|
| Scored questions | 1,540 / 1,540 |
| Correct | 1,272 |
| Accuracy | 82.5974% |
| Final infrastructure failures | 0 |
| Base successes reused | 823 |
| Failed-only repairs | 717 |

The base and repair runs remain separate immutable artifacts. The composite is
accepted only because the repair selection exactly equals the base
infrastructure-failure set and all artifact-facing benchmark contracts match.
The public bundle exposes privacy-safe outcomes and receipts rather than the
licensed questions, answers, retrieved context, or raw provider responses.

Two questions exhausted the grounded answer contract and were formally scored
wrong without reaching the three-vote judge. Therefore “all 1,540 questions
received three judge votes” is not a valid claim. The accurate statement is
that all 1,540 questions received a formal scored outcome and 1,538 reached the
three-vote judge.

## Frozen inputs

| File | Purpose |
|---|---|
| `diagnostic-40-v23.json` | Three-arm paid non-regression selection gate |
| `diagnostic-160-holdout-v23.json` | Disjoint provider-free retrieval holdout |
| `diagnostic-200-v23.json` | Selected-mode promotion run |
| `full-1540-v23.json` | Every scored category 1-4 question |
| `repair-717-v23-d19793c.json` | Exact failed-ID selection for the published base run only |

Do not reuse the checked-in 717-question repair selection for a different base
run. A new base run either completes cleanly or requires a newly generated,
explicit failed-ID question set whose inventory exactly matches that run.

The V23 protocol freezes the dataset digest, question inventory, answer and
judge contracts, top-k, worker and resource limits, retrieval planner,
DashScope embedding identity, local CrossEncoder identity, context renderer,
and checkpoint policy. A changed contract requires a new question-set version
or ADR; it is not a resume.

## 1. Verify the external dataset

```bash
mkdir -p benchmarks/locomo/data
curl -fsSL \
  https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json \
  -o benchmarks/locomo/data/locomo10.json
echo "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4  benchmarks/locomo/data/locomo10.json" \
  | shasum -a 256 -c -
```

Set the dataset path once:

```bash
DATASET="benchmarks/locomo/data/locomo10.json"
DATASET_SHA256="79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
```

## 2. Freeze a clean checkout and provider configuration

Paid evaluation must start from a clean, immutable checkout:

```bash
test -z "$(git status --porcelain=v1 --untracked-files=normal)"
COMMIT="$(git rev-parse --verify HEAD)"
```

Before every paid or provider-backed command, repeat both checks. Do not pass a
different commit string merely to satisfy the CLI field.

V23 uses these provider roles:

- DashScope `text-embedding-v4`, 1,024 dimensions, for corpus and query vectors;
- pinned local `Xenova/ms-marco-MiniLM-L-6-v2` for both reranking passes;
- `deepseek-v4-flash` with thinking disabled for semantic projection, answers,
  and judge votes.

Configure credentials through environment variables, never command arguments:

```bash
export DASHSCOPE_API_KEY="<dashscope-key>"
export DEEPSEEK_API_KEY="<deepseek-key>"

export CODECAIRN_EMBEDDING_MODEL="text-embedding-v4"
export CODECAIRN_EMBEDDING_DIMENSION="1024"
export CODECAIRN_RERANKER_MODEL="Xenova/ms-marco-MiniLM-L-6-v2"
export CODECAIRN_RERANKER_REVISION="a09144355adeed5f58c8ed011d209bf8ee5a1fec"

export CODECAIRN_SEMANTICIZER_PROFILE="structured"
export CODECAIRN_SEMANTIC_API_KEY="$DEEPSEEK_API_KEY"
export CODECAIRN_SEMANTIC_PROFILE="deepseek"
export CODECAIRN_SEMANTIC_BASE_URL="https://api.deepseek.com"
export CODECAIRN_SEMANTIC_MODEL="deepseek-v4-flash"
export CODECAIRN_SEMANTIC_THINKING="disabled"

export CODECAIRN_ANSWER_API_KEY="$DEEPSEEK_API_KEY"
export CODECAIRN_ANSWER_PROFILE="deepseek"
export CODECAIRN_ANSWER_BASE_URL="https://api.deepseek.com"
export CODECAIRN_ANSWER_MODEL="deepseek-v4-flash"
export CODECAIRN_ANSWER_THINKING="disabled"
export CODECAIRN_JUDGE_API_KEY="$DEEPSEEK_API_KEY"
export CODECAIRN_JUDGE_PROFILE="deepseek"
export CODECAIRN_JUDGE_BASE_URL="https://api.deepseek.com"
export CODECAIRN_JUDGE_MODEL="deepseek-v4-flash"
export CODECAIRN_JUDGE_THINKING="disabled"
```

The complete effective configuration is written to immutable manifests. API
keys are not.

## 3. Build and verify shared representation artifacts

Build one content-addressed corpus and one query-vector artifact from the full
V23 selection. The commands print the published directories; use those exact
paths in later steps.

```bash
uv run codecairn eval build-locomo-corpus "$DATASET" \
  --question-set benchmarks/locomo/full-1540-v23.json \
  --corpus-id "locomo-full-1540-v23-$COMMIT" \
  --repository-commit "$COMMIT" \
  --expected-dataset-sha256 "$DATASET_SHA256" \
  --output-root benchmark_results/locomo/corpora

uv run codecairn eval build-locomo-query-vectors "$DATASET" \
  --question-set benchmarks/locomo/full-1540-v23.json \
  --vector-set-id "locomo-full-1540-v23-$COMMIT" \
  --expected-dataset-sha256 "$DATASET_SHA256" \
  --output-root benchmark_results/locomo/query-vectors
```

Set `CORPUS` and `QUERIES` to the emitted content-addressed directories.
`--resume` is legal only with the identical command, checkout, provider
identity, and frozen question-set contract. Start-only attempt journals fail
closed when provider spend is ambiguous.

Diagnostic runs may reuse a full-selection vector artifact only when the
verifier accepts it as an immutable superset. They must still use the exact V23
diagnostic question-set file for their run inventory.

## 4. Pass staged promotion gates

The required order is:

```text
40-question retrieval canary
  -> 160-question disjoint retrieval holdout
  -> 40-question three-arm paid selection
  -> selected-mode 200-question promotion
  -> full 1,540-question run
```

First run the two provider-free retrieval gates:

```bash
CANARY_ID="locomo-diagnostic-40-v23-hierarchy-retrieval-$COMMIT"
HOLDOUT_ID="locomo-diagnostic-160-v23-hierarchy-retrieval-$COMMIT"
CANARY_RUN="benchmark_results/locomo/$CANARY_ID"
HOLDOUT_RUN="benchmark_results/locomo/$HOLDOUT_ID"

CODECAIRN_RECALL_MODE=hierarchy \
uv run codecairn eval run locomo "$DATASET" \
  --question-set benchmarks/locomo/diagnostic-40-v23.json \
  --run-id "$CANARY_ID" \
  --repository-commit "$COMMIT" \
  --output-root benchmark_results \
  --root "benchmark_results/runtime-$CANARY_ID" \
  --corpus "$CORPUS" \
  --query-vectors "$QUERIES" \
  --expected-dataset-sha256 "$DATASET_SHA256" \
  --mode retrieval \
  --max-workers 10

CODECAIRN_RECALL_MODE=hierarchy \
uv run codecairn eval run locomo "$DATASET" \
  --question-set benchmarks/locomo/diagnostic-160-holdout-v23.json \
  --run-id "$HOLDOUT_ID" \
  --repository-commit "$COMMIT" \
  --output-root benchmark_results \
  --root "benchmark_results/runtime-$HOLDOUT_ID" \
  --corpus "$CORPUS" \
  --query-vectors "$QUERIES" \
  --expected-dataset-sha256 "$DATASET_SHA256" \
  --mode retrieval \
  --max-workers 10

uv run codecairn eval report-locomo-evidence "$CANARY_RUN" \
  --dataset "$DATASET" \
  --output "$CANARY_RUN/evidence-coverage.json"
uv run codecairn eval report-locomo-evidence "$HOLDOUT_RUN" \
  --dataset "$DATASET" \
  --output "$HOLDOUT_RUN/evidence-coverage.json"
```

Then run the three paid 40-question variants and select one:

```bash
for MODE in episode-only hierarchy-no-neighbors hierarchy; do
  RUN_ID="locomo-diagnostic-40-v23-$MODE-$COMMIT"
  CODECAIRN_RECALL_MODE="$MODE" \
  uv run codecairn eval run locomo "$DATASET" \
    --question-set benchmarks/locomo/diagnostic-40-v23.json \
    --run-id "$RUN_ID" \
    --repository-commit "$COMMIT" \
    --output-root benchmark_results \
    --root "benchmark_results/runtime-$RUN_ID" \
    --corpus "$CORPUS" \
    --query-vectors "$QUERIES" \
    --retrieval-gate-question-set benchmarks/locomo/diagnostic-200-v23.json \
    --retrieval-canary-run "$CANARY_RUN" \
    --retrieval-holdout-run "$HOLDOUT_RUN" \
    --expected-dataset-sha256 "$DATASET_SHA256" \
    --mode full \
    --model deepseek-v4-flash \
    --judge-model deepseek-v4-flash \
    --max-workers 10
done

SELECTION_REPORT="benchmark_results/locomo/locomo-diagnostic-40-v23-$COMMIT-selection.json"
uv run codecairn eval compare-locomo \
  benchmarks/locomo/diagnostic-40-v23.json \
  --episode-only-run \
    "benchmark_results/locomo/locomo-diagnostic-40-v23-episode-only-$COMMIT" \
  --hierarchy-no-neighbors-run \
    "benchmark_results/locomo/locomo-diagnostic-40-v23-hierarchy-no-neighbors-$COMMIT" \
  --hierarchy-run \
    "benchmark_results/locomo/locomo-diagnostic-40-v23-hierarchy-$COMMIT" \
  --output "$SELECTION_REPORT"

test "$(jq -r '.gate_passed' "$SELECTION_REPORT")" = "true"
SELECTED_MODE="$(jq -r '.selected_variant' "$SELECTION_REPORT")"
```

Run and verify the selected 200-question promotion:

```bash
PROMOTION_ID="locomo-diagnostic-200-v23-$SELECTED_MODE-$COMMIT"
PROMOTION_RUN="benchmark_results/locomo/$PROMOTION_ID"
PROMOTION_REPORT="$PROMOTION_RUN-promotion.json"

CODECAIRN_RECALL_MODE="$SELECTED_MODE" \
uv run codecairn eval run locomo "$DATASET" \
  --question-set benchmarks/locomo/diagnostic-200-v23.json \
  --run-id "$PROMOTION_ID" \
  --repository-commit "$COMMIT" \
  --output-root benchmark_results \
  --root "benchmark_results/runtime-$PROMOTION_ID" \
  --corpus "$CORPUS" \
  --query-vectors "$QUERIES" \
  --retrieval-gate-question-set benchmarks/locomo/diagnostic-200-v23.json \
  --retrieval-canary-run "$CANARY_RUN" \
  --retrieval-holdout-run "$HOLDOUT_RUN" \
  --expected-dataset-sha256 "$DATASET_SHA256" \
  --mode full \
  --model deepseek-v4-flash \
  --judge-model deepseek-v4-flash \
  --max-workers 10

uv run codecairn eval promote-locomo \
  benchmarks/locomo/diagnostic-200-v23.json \
  --selection-report "$SELECTION_REPORT" \
  --episode-only-run \
    "benchmark_results/locomo/locomo-diagnostic-40-v23-episode-only-$COMMIT" \
  --hierarchy-no-neighbors-run \
    "benchmark_results/locomo/locomo-diagnostic-40-v23-hierarchy-no-neighbors-$COMMIT" \
  --hierarchy-run \
    "benchmark_results/locomo/locomo-diagnostic-40-v23-hierarchy-$COMMIT" \
  --run "$PROMOTION_RUN" \
  --output "$PROMOTION_REPORT"

test "$(jq -r '.gate_passed' "$PROMOTION_REPORT")" = "true"
```

`promote-locomo` reopens and verifies every named run instead of trusting the
comparison JSON.

The 200-question V23 gate requires at least 78% overall, 70% multi-hop, 68%
open-domain, at most a two-point single-hop regression from the frozen 92%
baseline, zero infrastructure failures, retrieval P95 at most 4,000 ms, and
process RSS below 2 GiB. The question-set and verifier are authoritative; do
not copy thresholds from the historical V19 runbook.

## 5. Run the full V23 selection

Only after the selected 200-question promotion passes:

```bash
SELECTED_MODE="<mode-from-the-verified-promotion-report>"
BASE_RUN_ID="locomo-full-1540-v23-$(git rev-parse --short HEAD)-formal-$SELECTED_MODE"

CODECAIRN_RECALL_MODE="$SELECTED_MODE" \
uv run codecairn eval run locomo "$DATASET" \
  --question-set benchmarks/locomo/full-1540-v23.json \
  --run-id "$BASE_RUN_ID" \
  --repository-commit "$COMMIT" \
  --output-root benchmark_results \
  --root "benchmark_results/runtime-$BASE_RUN_ID" \
  --corpus "$CORPUS" \
  --query-vectors "$QUERIES" \
  --expected-dataset-sha256 "$DATASET_SHA256" \
  --mode full \
  --model deepseek-v4-flash \
  --judge-model deepseek-v4-flash \
  --max-workers 10
```

The full V23 question set deliberately has no paid-scoring-gate field, so this
command does not accept a diagnostic retrieval receipt. The verified
`PROMOTION_REPORT` is the human/release precondition for launching it and must
be retained alongside the full-run lineage.

An interrupted run resumes only with the identical command plus `--resume`.
Never copy or edit question checkpoints to force completion.

## 6. Repair provider failures exactly

If the full run contains infrastructure failures:

1. preserve the base run unchanged;
2. derive a new `explicit-question-ids-v1` selection from exactly its failed
   IDs and freeze its counts and digest;
3. run only that selection under the same artifact-facing contract;
4. require the repair to score every selected ID with zero infrastructure
   failures; and
5. compose without mutating either source run.

The current CLI does not generate the failed-ID question-set JSON. Creating and
reviewing that immutable `explicit-question-ids-v1` asset is an explicit
protocol-authoring step; the loader and composer validate its dataset,
inventory, category counts, selection digest, and exact equality with the
base failure set before accepting it.

```bash
REPAIR_QUESTION_SET="path/to/new-exact-failed-id-selection.json"
REPAIR_COMMIT="$(git rev-parse --verify HEAD)"
test -z "$(git status --porcelain=v1 --untracked-files=normal)"
REPAIR_RUN_ID="locomo-repair-$(git rev-parse --short HEAD)-formal-$SELECTED_MODE"

CODECAIRN_RECALL_MODE="$SELECTED_MODE" \
uv run codecairn eval run locomo "$DATASET" \
  --question-set "$REPAIR_QUESTION_SET" \
  --run-id "$REPAIR_RUN_ID" \
  --repository-commit "$REPAIR_COMMIT" \
  --output-root benchmark_results \
  --root "benchmark_results/runtime-$REPAIR_RUN_ID" \
  --corpus "$CORPUS" \
  --query-vectors "$QUERIES" \
  --expected-dataset-sha256 "$DATASET_SHA256" \
  --mode full \
  --model deepseek-v4-flash \
  --judge-model deepseek-v4-flash \
  --max-workers 10

uv run codecairn eval compose-locomo-repair \
  benchmarks/locomo/full-1540-v23.json \
  --repair-question-set "$REPAIR_QUESTION_SET" \
  --base-run "benchmark_results/locomo/$BASE_RUN_ID" \
  --repair-run "benchmark_results/locomo/$REPAIR_RUN_ID" \
  --output "benchmark_results/locomo/$BASE_RUN_ID-composite.json"
```

`compose-locomo-repair` rejects missing, extra, overlapping, failed, or
contract-changing repairs. Repository commits may differ, but dataset, corpus,
query vectors, retrieval, generation, judging, checkpoint, and question
contracts may not. The repair question set inherits the ungated full-run
protocol, so the repair command must not supply diagnostic retrieval-gate
arguments.

For the already-published v3 lineage only, the frozen repair input is
`repair-717-v23-d19793c.json`. It is evidence of that run, not a reusable
template.

## 7. Verify and publish claims

Read an ordinary run without mutation:

```bash
uv run codecairn eval report locomo \
  "benchmark_results/locomo/$BASE_RUN_ID"
```

Build a public bundle only from independently verified suite artifacts and
quality receipts. The bundle generator accepts either an ordinary LoCoMo run
directory or a verified exact-repair composite. Offline verification must pass
before any number is published:

```bash
uv run codecairn evidence verify evidence/benchmark-v3
```

Offline verification proves inventory integrity, privacy-safe source outcomes,
exact-repair partitioning, aggregate recomputation, and generated-document
reproduction. It does not rerun providers, independently judge semantic
correctness, or prove the current public import-to-index-to-recall product
lifecycle.

## Protocol sources

- [ADR 0034](../../docs/adr/0034-v23-normalizes-safe-insufficient-answer-shapes.md)
  defines V23 answer normalization.
- [ADR 0035](../../docs/adr/0035-v23-freezes-the-standard-1540-question-locomo-run.md)
  freezes the full selection.
- [ADR 0037](../../docs/adr/0037-locomo-provider-failures-use-exact-repair-runs.md)
  defines exact repair.
- [ADR 0039](../../docs/adr/0039-public-evidence-publishes-exact-repair-outcomes.md)
  defines public composite verification.
- [`../../docs/evaluation/README.md`](../../docs/evaluation/README.md) defines
  cross-suite evidence boundaries.
