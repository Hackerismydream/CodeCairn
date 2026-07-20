# LoCoMo Adapter

CodeCairn evaluates end-to-end memory question answering against the public
ten-conversation LoCoMo release. The dataset remains an external input because
it is licensed CC BY-NC 4.0; it is not redistributed in this repository.

Download and verify the pinned release:

```bash
mkdir -p benchmarks/locomo/data
curl -fsSL \
  https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json \
  -o benchmarks/locomo/data/locomo10.json
echo "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4  benchmarks/locomo/data/locomo10.json" \
  | shasum -a 256 -c -
```

The loader preserves conversation identifiers, session boundaries, original
session timestamps, speakers, questions, categories, evidence dialog IDs, gold
answers, and adversarial annotations. Text and available image captions become
attributed exact-quote memories through `MemoryRuntime.evaluate_proposal`; each
conversation uses its own runtime root.

The default scored protocol includes categories 1 through 4 and retains but
does not score category 5. This follows the declared CodeCairn protocol rather
than silently treating missing category-5 `answer` fields as failures. Category
mapping is recorded in every report. Smoke runs answer one question per selected
conversation, perform no judge calls, and are always marked unscored.

## DeepSeek full protocol

The quality-first DeepSeek protocol uses `deepseek-v4-pro` for both answer and
judge roles, thinking mode with `high` reasoning effort, three judge calls per
question, categories 1 through 4, hybrid retrieval at top 20, seed 17 for local
question ordering, and conversation-level concurrency. DeepSeek does not
receive the local seed because its documented chat-completions request does not
declare that field. The manifest records both role configurations and the CNY
token prices used for the cost estimate.

Export credentials outside shell history, then start one immutable run:

```bash
export DEEPSEEK_API_KEY="..."
COMMIT="$(git rev-parse HEAD)"
uv run codecairn eval run locomo benchmarks/locomo/data/locomo10.json \
  --run-id locomo-full-deepseek-v4-pro-20260719 \
  --repository-commit "$COMMIT" \
  --output-root benchmark_results \
  --root benchmark_results/runtime-control \
  --mode full \
  --model deepseek-v4-pro \
  --judge-model deepseek-v4-pro \
  --max-workers 10
```

An interrupted run is resumed with the identical command plus `--resume`.
Resume is missing-checkpoint-only: completed ingest and question artifacts are
never overwritten, and any configuration drift from the original manifest is
rejected. A completed run is publishable only when all 1,540 selected questions
are scored, each has three valid votes, and infrastructure failures are zero.

## Frozen 200-question diagnostic

Before a new retrieval stack may spend a full 1,540-question run, execute the
three-layer ablation frozen in `diagnostic-200.json`. The selector takes 50
questions from each scored category with a dataset-pinned SHA-256 ordering; its
expected selection digest prevents a seed, loader, or question-identity change
from silently moving the diagnostic set. Question text is not redistributed.
Initial conversation ingestion uses the same Markdown truth and rebuild parity
contract as production recovery, but projects all Episode and AtomicFact
documents through bounded Qwen embedding batches. Ingestion and index rebuilding
are serialized to bound Arrow/LanceDB peak memory. The local CrossEncoder uses
one manifest-recorded inference thread and disables tokenizer parallelism.
After all ten ingest checkpoints exist, a fresh process runs the question phase;
one caller thread performs every local retrieval while DeepSeek answer and judge
calls are pipelined in a separate pool at `--max-workers`; this avoids one set
of LanceDB, Arrow, and ONNX native caches per API worker. The manifest records
the requested API concurrency, `ingest_max_workers = 1`,
`retrieval_max_workers = 1`, and `retrieval_thread_count = 1`.

Run the same commit, answer model, judge model, vote count, and top-k under the
three declared recall modes. Each command must include:

```bash
--question-set benchmarks/locomo/diagnostic-200.json
```

The reproducible launch sequence is:

```bash
COMMIT="$(git rev-parse HEAD)"
for MODE in episode-only hierarchy-no-neighbors hierarchy; do
  CODECAIRN_RECALL_MODE="$MODE" uv run codecairn eval run locomo \
    benchmarks/locomo/data/locomo10.json \
    --question-set benchmarks/locomo/diagnostic-200.json \
    --run-id "locomo-diagnostic-200-v6-$MODE" \
    --repository-commit "$COMMIT" \
    --output-root benchmark_results \
    --root "benchmark_results/runtime-v6-$MODE" \
    --mode full \
    --model deepseek-v4-flash \
    --judge-model deepseek-v4-flash \
    --max-workers 10 \
    --execution-phase ingest
  CODECAIRN_RECALL_MODE="$MODE" uv run codecairn eval run locomo \
    benchmarks/locomo/data/locomo10.json \
    --question-set benchmarks/locomo/diagnostic-200.json \
    --run-id "locomo-diagnostic-200-v6-$MODE" \
    --repository-commit "$COMMIT" \
    --output-root benchmark_results \
    --root "benchmark_results/runtime-v6-$MODE" \
    --mode full \
    --model deepseek-v4-flash \
    --judge-model deepseek-v4-flash \
    --max-workers 10 \
    --execution-phase questions \
    --resume
done

uv run codecairn eval compare-locomo \
  benchmarks/locomo/diagnostic-200.json \
  --episode-only-run benchmark_results/locomo/locomo-diagnostic-200-v6-episode-only \
  --hierarchy-no-neighbors-run \
    benchmark_results/locomo/locomo-diagnostic-200-v6-hierarchy-no-neighbors \
  --hierarchy-run benchmark_results/locomo/locomo-diagnostic-200-v6-hierarchy \
  --output benchmark_results/locomo/locomo-diagnostic-200-v6-report.json
```

The comparison gate requires 200 scored questions and zero infrastructure
failures per variant. Full hierarchy must improve at least 2.0 accuracy points
over Episode-only recall, may regress by at most 1.0 point against hierarchy
without neighbors, and must keep retrieval P95 at or below 2,500 ms. A failed
gate is a diagnostic result, not permission to launch the full run.

DeepSeek model capabilities and pricing are sourced from the
[official model and pricing page](https://api-docs.deepseek.com/quick_start/pricing/);
request fields and usage breakdowns follow the
[official chat completion schema](https://api-docs.deepseek.com/api/create-chat-completion/).

LoCoMo was introduced by Maharana et al., “Evaluating Very Long-Term
Conversational Memory of LLM Agents,” ACL 2024. See the
[upstream repository](https://github.com/snap-research/locomo) and
[paper](https://aclanthology.org/2024.acl-long.747/).
