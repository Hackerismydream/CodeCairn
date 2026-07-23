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
attributed exact-quote memories through `MemoryRuntime.write_episode`; each
conversation uses its own runtime root.

The default scored protocol includes categories 1 through 4 and retains but
does not score category 5. This follows the declared CodeCairn protocol rather
than silently treating missing category-5 `answer` fields as failures. Category
mapping is recorded in every report. Smoke runs answer one question per selected
conversation, perform no judge calls, and are always marked unscored.

## Cost-controlled v15 protocol

The v15 protocol separates one-time representation cost, provider-free
retrieval measurement, and answer/judge cost. `deepseek-v4-flash` performs
structured semantic projection, answers, and three judge votes. Semantic
projection and scoring may disable thinking independently; the manifest records
the effective model configuration and token usage. DashScope
`text-embedding-v4` creates 1,024-dimensional document and query vectors.

### Historical diagnostics

The earlier scored 200-question run
`locomo-v5-diagnostic200-hierarchy-d5fb39c` completed at 139/200 (69.5%):
52% multi-hop, 80% temporal, 54% open-domain, and 92% single-hop. It predates
the v14 retrieval protocol and is not a v14 or v15 score.

The subsequent immutable retrieval-only run
`locomo-diagnostic-200-v14-hierarchy-retrieval-efe76a7` completed all 200
questions with zero infrastructure failures and no answer or judge calls.
Complete gold evidence reached ranked parents for 178/192 resolvable questions
(92.71%), candidate snippets for 157/192 (81.77%), and final context for
136/192 (70.83%). Retrieval P95 was 2,868.51 ms, so v14 failed both its 85%
complete-context coverage gate and its 2,500 ms latency gate. Maximum observed
RSS was 1,040,449,536 bytes, below the 2 GiB limit.

These v14 artifacts remain unchanged historical evidence. V15 has new protocol
and run IDs and has not yet produced a verified coverage, latency, accuracy, or
cost result. [ADR 0022](../../docs/adr/0022-v15-rebalances-fact-selection-and-context-budgeting.md)
records the failure funnel and the v15 design response.

Credentials are exported outside shell history. Build one structured semantic
corpus and reuse it for every recall variant:

```bash
export DASHSCOPE_API_KEY="..."
export DEEPSEEK_API_KEY="..."

export CODECAIRN_RETRIEVAL_PROFILE=dashscope
export CODECAIRN_RECALL_MODE=hierarchy
export CODECAIRN_EMBEDDING_API_KEY="$DASHSCOPE_API_KEY"
export CODECAIRN_EMBEDDING_MODEL=text-embedding-v4
export CODECAIRN_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export CODECAIRN_EMBEDDING_DIMENSION=1024
export CODECAIRN_EMBEDDING_REVISION=provider-managed
export CODECAIRN_EMBEDDING_LICENSE="Alibaba Cloud Model Studio service"
export CODECAIRN_EMBEDDING_BATCH_SIZE=10
export CODECAIRN_EMBEDDING_INPUT_PRICE_CNY_PER_MILLION=0.5
export CODECAIRN_EMBEDDING_TIMEOUT_SECONDS=30
export CODECAIRN_EMBEDDING_MAX_ATTEMPTS=3
export CODECAIRN_EMBEDDING_RETRY_BACKOFF_SECONDS=1

export CODECAIRN_RERANKER_MODEL=Xenova/ms-marco-MiniLM-L-6-v2
export CODECAIRN_RERANKER_SOURCE=Xenova/ms-marco-MiniLM-L-6-v2
export CODECAIRN_RERANKER_REVISION=a09144355adeed5f58c8ed011d209bf8ee5a1fec
export CODECAIRN_RERANKER_LICENSE=Apache-2.0
export CODECAIRN_RERANKER_BATCH_SIZE=8

export CODECAIRN_EVAL_MAX_RSS_BYTES=2147483648
export CODECAIRN_EVAL_WORKER_STALL_SECONDS=600
export CODECAIRN_EVAL_WORKER_POLL_SECONDS=0.25
export CODECAIRN_EVAL_WORKER_RSS_POLL_SECONDS=1

export CODECAIRN_SEMANTICIZER_PROFILE=structured
export CODECAIRN_SEMANTIC_API_KEY="$DEEPSEEK_API_KEY"
export CODECAIRN_SEMANTIC_PROFILE=deepseek
export CODECAIRN_SEMANTIC_BASE_URL=https://api.deepseek.com
export CODECAIRN_SEMANTIC_MODEL=deepseek-v4-flash
export CODECAIRN_SEMANTIC_THINKING=disabled
export CODECAIRN_SEMANTIC_REVISION=grounded-clause-json-v2
export CODECAIRN_SEMANTIC_MAX_FACTS_PER_REQUEST=48
export CODECAIRN_SEMANTIC_MAX_REQUEST_CHARS=48000
export CODECAIRN_SEMANTIC_MAX_RESPONSE_CHARS=96000
unset CODECAIRN_SEMANTIC_REASONING_EFFORT CODECAIRN_SEMANTIC_MAX_TOKENS

if [ -n "$(git status --porcelain=v1 --untracked-files=normal)" ]; then
  echo "refusing a paid LoCoMo build from a dirty checkout" >&2
  exit 1
fi

COMMIT="$(git rev-parse --verify HEAD)"
require_frozen_checkout() {
  if [ -n "$(git status --porcelain=v1 --untracked-files=normal)" ] || \
     [ "$(git rev-parse --verify HEAD)" != "$COMMIT" ]; then
    echo "checkout changed after COMMIT was frozen; refusing provider calls" >&2
    return 1
  fi
}

require_frozen_checkout || exit 1
uv run codecairn eval build-locomo-corpus \
  benchmarks/locomo/data/locomo10.json \
  --question-set benchmarks/locomo/diagnostic-200-v15.json \
  --corpus-id "locomo-grounded-clause-v7" \
  --repository-commit "$COMMIT" \
  --output-root benchmark_results/locomo/corpora
```

`--question-set` is mandatory for every paid corpus build in this protocol. The
builder loads it before creating the corpus output directory or invoking the
corpus memory factory, verifies the active embedding, reranker, planner, and
mode-specific neighbor-window contract, and binds the verified definition and
protocol digests into the immutable corpus build contract.

The corpus is published only after every conversation has a matching semantic
projection receipt, truth/index fingerprints match, and the index queue is
idle. `--resume` reuses only verified conversation checkpoints whose projection
contract matches exactly. It never combines different model, prompt, limit, or
cache identities.

## Frozen 200-question diagnostic

Before a new retrieval stack may spend a full 1,540-question run, execute the
staged diagnostic frozen in `diagnostic-200-v15.json`. The selector takes 50
questions from each scored category with a dataset-pinned SHA-256 ordering; its
expected selection digest prevents a seed, loader, or question-identity change
from silently moving the diagnostic set. Question text is not redistributed.
Initial conversation ingestion uses the same Markdown truth and rebuild parity
contract as production recovery, but projects all Episode and AtomicFact
documents through bounded Qwen embedding batches. It publishes one immutable,
content-addressed corpus after verifying truth/index fingerprints and an idle
index queue. All variants reuse that corpus. Query vectors are also frozen once
per question selection and fail closed on a miss; scored runs cannot silently
call the embedding provider. The local CrossEncoder uses two manifest-recorded
inference threads, disables tokenizer parallelism, performs one local warmup
before question timing, records that warmup in worker resource evidence, and
length-sorts documents before batching to reduce padding work. After parent
ranking, it performs one bounded, dialogue-aware fact pass: at most 256 facts
globally; every parent receives up to a 12-candidate breadth floor before spare
work is assigned by direct-match count, fact capacity, and rank; no parent
supplies more than 24 candidates or 12 selected facts; and no reranker document
exceeds 2,048 characters. A short or anaphoric candidate may include
the preceding other-speaker turn alongside an evidence-linked semantic
projection and the candidate's exact turn. Semantic projection is ranking
metadata only. Context admission applies a bounded `2.0` prior only to scored
direct matches from their own parent; raw scores remain unchanged. The
facts-first compiler renders complete exact attributed source facts, including
timestamps, and admits UTF-8 bytes under the pinned 4,000-token upper-bound
contract without per-line rounding. One caller thread performs every local
retrieval while
DeepSeek answer and judge calls are pipelined in a separate pool at
`--max-workers`.
Shared-corpus runs execute one conversation per fresh Python process. Each
worker verifies the frozen artifacts, queries an isolated copy of that
conversation runtime, and publishes its whole checkpoint
directory only after the 2 GiB hard RSS gate and exact question inventory pass.
The coordinator writes a start receipt before launching workers and a matching
completion receipt on every handled exit; an unmatched start receipt makes the
run ineligible for reporting. Failed attempts freeze checkpoint hashes, so a
resume can reuse verified completed questions without repeating paid calls.
Checkpoint policy `journal-replay-or-unknown-spend-fail-closed-v3` also binds
every answer and judge application attempt to an fsynced start record and every
observed provider attempt to an fsynced outcome. Connect, connect-timeout, and
pool-timeout failures may retry because no request was accepted; read, write,
and remote-protocol failures are ambiguous and therefore become start-only
unknown spend without an automatic transport retry. A successful HTTP response
that cannot be parsed into a usage-bearing model response is handled the same
way instead of being counted as a free failure. Priced answer and judge calls
must expose complete input/cache/output usage and a recomputable
currency-specific cost; reporting rejects partial accounting. An invalid judge
vote
ends that question immediately instead of spending the remaining votes.
Corrupt attempt journals, or valid journals that cannot be bound to an
immutable worker receipt and question checkpoint, block resume from launching
a replacement worker.

Run the same commit, answer model, judge model, vote count, and top-k under the
declared recall modes. Each command must include:

```bash
--question-set benchmarks/locomo/diagnostic-200-v15.json
```

Freeze the diagnostic query vectors once after publishing the corpus:

```bash
require_frozen_checkout || exit 1
uv run codecairn eval build-locomo-query-vectors \
  benchmarks/locomo/data/locomo10.json \
  --question-set benchmarks/locomo/diagnostic-200-v15.json \
  --vector-set-id "locomo-diagnostic-200-v15" \
  --output-root benchmark_results/locomo/query-vectors
```

The query builder sends provider-sized batches instead of one request per
question. Before every batch it persists an immutable start receipt; after a
response it persists vectors, provider-attempt counts, reported input tokens,
and CNY cost in a batch checkpoint. `--resume` skips verified checkpoints. A
start receipt without a matching checkpoint fails closed as unknown provider
spend, so recovery never silently repeats a possibly billed request. The
embedding Adapter also stops on read, write, or protocol transport failures;
only connection failures known to precede provider acceptance may retry. The
configured input price is part of the embedding identity; update it when the
provider price changes.

Use the content-addressed directories printed by those commands:

A previously verified v7 corpus and frozen query-vector artifact remain valid
for v15 when their dataset, selection, semantic projection, embedding model,
revision, and dimension match. The v15 run still binds the new question-set
digest, selector limits, renderer, retrieval configuration, and repository
commit; changing context selection alone does not justify paid re-embedding.

```bash
CORPUS="benchmark_results/locomo/corpora/corpus-<content-sha-prefix>"
QUERIES="benchmark_results/locomo/query-vectors/queries-<content-sha-prefix>"

require_frozen_checkout || exit 1
CODECAIRN_RECALL_MODE=hierarchy uv run codecairn eval run locomo \
  benchmarks/locomo/data/locomo10.json \
  --question-set benchmarks/locomo/diagnostic-200-v15.json \
  --run-id "locomo-diagnostic-200-v15-hierarchy-retrieval" \
  --repository-commit "$COMMIT" \
  --output-root benchmark_results \
  --root benchmark_results/runtime-v15-hierarchy-retrieval \
  --corpus "$CORPUS" \
  --query-vectors "$QUERIES" \
  --mode retrieval \
  --max-workers 10

uv run codecairn eval report-locomo-evidence \
  benchmark_results/locomo/locomo-diagnostic-200-v15-hierarchy-retrieval \
  --dataset benchmarks/locomo/data/locomo10.json \
  --output \
  benchmark_results/locomo/locomo-diagnostic-200-v15-hierarchy-retrieval/evidence-coverage.json
```

This first run performs no answer or judge calls. Proceed to paid scoring only
when complete gold evidence reaches context for at least 85% of resolvable
questions, every context remains at or below 4,000 pinned tokens, retrieval P95
is at most 2,500 ms, RSS remains below 2 GiB, and infrastructure failures are
zero. A deterministic 40-question stratified slice is scored before the full
200-question diagnostic.

Disable answer and judge thinking for the low-cost slice and diagnostic unless
an explicitly separate reasoning ablation is being measured:

```bash
export CODECAIRN_ANSWER_API_KEY="$DEEPSEEK_API_KEY"
export CODECAIRN_ANSWER_PROFILE=deepseek
export CODECAIRN_ANSWER_BASE_URL=https://api.deepseek.com
export CODECAIRN_ANSWER_MODEL=deepseek-v4-flash
export CODECAIRN_ANSWER_THINKING=disabled
unset CODECAIRN_ANSWER_REASONING_EFFORT CODECAIRN_ANSWER_MAX_TOKENS

export CODECAIRN_JUDGE_API_KEY="$DEEPSEEK_API_KEY"
export CODECAIRN_JUDGE_PROFILE=deepseek
export CODECAIRN_JUDGE_BASE_URL=https://api.deepseek.com
export CODECAIRN_JUDGE_MODEL=deepseek-v4-flash
export CODECAIRN_JUDGE_THINKING=disabled
unset CODECAIRN_JUDGE_REASONING_EFFORT CODECAIRN_JUDGE_MAX_TOKENS

for MODE in episode-only hierarchy-no-neighbors hierarchy; do
  require_frozen_checkout || exit 1
  CODECAIRN_RECALL_MODE="$MODE" uv run codecairn eval run locomo \
    benchmarks/locomo/data/locomo10.json \
    --question-set benchmarks/locomo/diagnostic-40-v15.json \
    --run-id "locomo-diagnostic-40-v15-$MODE" \
    --repository-commit "$COMMIT" \
    --output-root benchmark_results \
    --root "benchmark_results/runtime-v15-$MODE-40" \
    --corpus "$CORPUS" \
    --query-vectors "$QUERIES" \
    --mode full \
    --model deepseek-v4-flash \
    --judge-model deepseek-v4-flash \
    --max-workers 10
done

uv run codecairn eval compare-locomo \
  benchmarks/locomo/diagnostic-40-v15.json \
  --episode-only-run \
    benchmark_results/locomo/locomo-diagnostic-40-v15-episode-only \
  --hierarchy-no-neighbors-run \
    benchmark_results/locomo/locomo-diagnostic-40-v15-hierarchy-no-neighbors \
  --hierarchy-run benchmark_results/locomo/locomo-diagnostic-40-v15-hierarchy \
  --output benchmark_results/locomo/locomo-diagnostic-40-v15-report.json
```

The 40-question protocol does not permit an older v5/v12 run or a run from a
different commit, corpus, query-vector set, or model configuration to stand in
for one of these controls. The comparison artifact exposes `gate_passed`,
`selected_variant`, and the selected commit/corpus/query-vector/model contract.
Promotion also reopens all three source run directories, recomputes their
reports, verifies each manifest SHA-256, and rejects a comparison JSON that
cannot be reproduced from those immutable artifacts.
If any of the three runs is unavailable or `gate_passed` is false, stop; do not
launch the paid 200-question diagnostic.

Only the variant selected by the successful 40-question comparison proceeds to
200 paid questions. Substitute that exact recall mode and run ID below. The
machine-readable promotion contract in `diagnostic-200-v15.json` requires at
least 78% overall, 70% multi-hop, 68% open-domain, zero infrastructure failures,
retrieval P95 at most 2,500 ms, and process RSS strictly below 2 GiB. Single-hop
is compared with the frozen v5 artifact over the same 200-question selection:
its verified 92% baseline permits at most a two-point regression, so the v15 run
must reach at least 90%.

```bash
SELECTED_MODE="<selected_variant-from-the-40-question-report>"
require_frozen_checkout || exit 1
CODECAIRN_RECALL_MODE="$SELECTED_MODE" uv run codecairn eval run locomo \
  benchmarks/locomo/data/locomo10.json \
  --question-set benchmarks/locomo/diagnostic-200-v15.json \
  --run-id "locomo-diagnostic-200-v15-$SELECTED_MODE" \
  --repository-commit "$COMMIT" \
  --output-root benchmark_results \
  --root "benchmark_results/runtime-v15-$SELECTED_MODE" \
  --corpus "$CORPUS" \
  --query-vectors "$QUERIES" \
  --mode full \
  --model deepseek-v4-flash \
  --judge-model deepseek-v4-flash \
  --max-workers 10

PROMOTION_REPORT="benchmark_results/locomo/locomo-diagnostic-200-v15-$SELECTED_MODE-promotion.json"
uv run codecairn eval promote-locomo \
  benchmarks/locomo/diagnostic-200-v15.json \
  --selection-report \
    benchmark_results/locomo/locomo-diagnostic-40-v15-report.json \
  --episode-only-run \
    benchmark_results/locomo/locomo-diagnostic-40-v15-episode-only \
  --hierarchy-no-neighbors-run \
    benchmark_results/locomo/locomo-diagnostic-40-v15-hierarchy-no-neighbors \
  --hierarchy-run \
    benchmark_results/locomo/locomo-diagnostic-40-v15-hierarchy \
  --run "benchmark_results/locomo/locomo-diagnostic-200-v15-$SELECTED_MODE" \
  --output "$PROMOTION_REPORT"

test "$(jq -r '.gate_passed' "$PROMOTION_REPORT")" = "true"
```

Outside the required 40-question selection gate, Episode-only and no-neighbor
comparisons should first run in `--mode retrieval` and use evidence coverage as
the free ablation signal. Do not repeat paid three-variant scoring during normal
iteration. The frozen 200-question stage runs only the variant selected by the
40-question comparison. `promote-locomo` rejects a different selected mode,
commit, corpus, query-vector artifact, answer model, judge model, question set,
worker RSS contract, or incomplete run before writing its immutable report.

An interrupted run resumes with the identical command plus `--resume`.
Completed ingest and question artifacts are never overwritten, and any
configuration drift is rejected. A 1,540-question run is publishable only after
the selected 200-question variant passes its gate, all selected questions are
scored, each has three valid votes, and infrastructure failures are zero.
The final report reconstructs the exact expected answer and judge journal entry
IDs from each question receipt. Extra entries, missing entries, or unknown
spend not represented by the matching infrastructure-failed question make the
run ineligible for reporting.

The worker coordinator polls durable checkpoints every 250 ms and samples live
RSS once per second. It also checks the child's reported `ru_maxrss`, records a
liveness heartbeat, and stops a worker after 600 seconds without a new durable
question checkpoint.
The CrossEncoder uses batches of 8 by default so hierarchical candidate sets do
not create a large ONNX activation spike; `CODECAIRN_RERANKER_BATCH_SIZE`
changes this frozen retrieval contract.

The comparison gate requires 40 scored questions and zero infrastructure
failures for each of the three variants. Hierarchy without temporal neighbors
must improve at least 2.0 accuracy points over Episode-only recall. Temporal
neighbors are selected only when overall accuracy does not decline, temporal or
multi-hop accuracy improves, and retrieval P95 rises by at most 20%. The
selected variant must keep retrieval P95 at or below 2,500 ms. The subsequent
200-question stage scores only that selected variant against the absolute
quality thresholds above. A failed gate is a diagnostic result, not permission
to launch the next paid stage.

DeepSeek model capabilities and CNY pricing are sourced from the
[official model and pricing page](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/);
request fields and usage breakdowns follow the
[official chat completion schema](https://api-docs.deepseek.com/api/create-chat-completion/).

LoCoMo was introduced by Maharana et al., “Evaluating Very Long-Term
Conversational Memory of LLM Agents,” ACL 2024. See the
[upstream repository](https://github.com/snap-research/locomo) and
[paper](https://aclanthology.org/2024.acl-long.747/).
