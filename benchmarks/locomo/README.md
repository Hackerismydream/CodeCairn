# LoCoMo v0.1 Runbook

CodeCairn keeps the licensed LoCoMo dataset outside Git and checks in only
selection/protocol definitions. Historical V19–V24 commands are retained in
[`HISTORICAL.md`](HISTORICAL.md); they are not executable product interfaces.

## Dataset

```bash
mkdir -p benchmarks/locomo/data
curl -fsSL \
  https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json \
  -o benchmarks/locomo/data/locomo10.json
echo "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4  benchmarks/locomo/data/locomo10.json" \
  | shasum -a 256 -c -
```

## Preflight without spending

```bash
make eval-locomo-200 HELP=1
make eval-locomo-full HELP=1
```

The plan resolves the current implementation SHA, checked-in
[`v01-protocol.json`](v01-protocol.json), frozen 200/1,540-question selection,
answer/judge/retrieval roles, expected output, credentials, and spend boundary.
HELP makes no provider call.

## Live run

Configure the retrieval profile plus independent OpenAI-compatible answer and
judge clients. Keys are environment-only:

```bash
export CODECAIRN_EMBEDDING_API_KEY="<key>"
export CODECAIRN_ANSWER_API_KEY="<key>"
export CODECAIRN_ANSWER_BASE_URL="https://api.deepseek.com"
export CODECAIRN_ANSWER_MODEL="deepseek-v4-flash"
export CODECAIRN_JUDGE_API_KEY="<key>"
export CODECAIRN_JUDGE_BASE_URL="https://api.deepseek.com"
export CODECAIRN_JUDGE_MODEL="deepseek-v4-flash"
```

Then run from a clean commit:

```bash
RUN_ID="<immutable-id>" \
SPEND_ACK=YES \
SPEND_CEILING_USD="<hard-ceiling>" \
MAX_CALL_COST_USD="<provider-upper-bound>" \
DATASET=benchmarks/locomo/data/locomo10.json \
make eval-locomo-200
```

Use `eval-locomo-full` only after inspecting the diagnostic artifact. The
runner reserves the worst-case bounded call count before starting, writes an
immutable started/finished journal around every provider attempt, and keeps
provider failures as infrastructure failures. A run containing such failures
preserves its artifact and exits nonzero.

The full release run is owned by task `v01-010`; the historical 82.60% bundle
cannot be relabeled as a v0.1 result.

## Exact repair

Generate an explicit question set from exactly the base run's infrastructure
failures, then run it as a separate immutable artifact:

```bash
uv run python -m codecairn.evaluation.locomo repair-selection \
  benchmark_results/<locomo-200-or-locomo-full>/<base-run> \
  benchmark_results/<locomo-200-or-locomo-full>/<base-run>-repair-selection.json
```

Compose only after that repair run scores every selected question:

```bash
uv run python -m codecairn.evaluation.locomo compose \
  benchmark_results/<locomo-200-or-locomo-full>/<base-run> \
  benchmark_results/<locomo-200-or-locomo-full>/<repair-run> \
  benchmark_results/<locomo-200-or-locomo-full>/<base-run>-composite.json
```

Composition accepts the pair only when repair IDs exactly match the base
failure set and dataset, protocol, retrieval, answer, and judge identities are
unchanged. Neither operation mutates the base run.
