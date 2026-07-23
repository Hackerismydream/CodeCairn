# CodeCairn

CodeCairn is an auditable long-term memory runtime for coding agents. It turns
Codex and Claude Code sessions into evidence-backed Coding Memories, stores
human-readable Markdown as the source of truth, and builds task-shaped Recall
Context for later coding work.

The project is intentionally narrow: it is a memory runtime, not an agent
runner, IDE, or cloud knowledge platform.

## Status

CodeCairn is under active development. Published benchmark numbers live in the
[public evidence bundle](evidence/benchmark-v1/README.md), where every headline
measurement links to its manifest, raw aggregate inputs, and verification
command. The bundle keeps the explicitly unscored LoCoMo smoke run separate
from the completed retrieval, recovery, and CodingMemoryBench measurements.

The first release is planned in three milestones:

1. Trace, import ledger, Markdown truth, and SQLite state.
2. Evidence-backed extraction, LanceDB indexing, and Recall Context.
3. LoCoMo evaluation, isolated coding-task A/B runs, and recovery evidence.

## Development

CodeCairn requires Python 3.12 and `uv`.

```bash
uv sync --all-groups
make check
```

The import path auto-detects Codex and Claude Code JSONL and emits one shared
Agent Trace. Deterministic Evidence Facts and type-specific gates accept
grounded User Preference and Repository Convention proposals while recording
rejections for audit. Verified Fix additionally requires a file change followed
by a successful test, lint, type-check, or build command; Debug Episode connects
one task, action, and observed outcome. Complete Evidence Fact snapshots remain
inside Markdown truth and rebuild into a Recall Episode plus AtomicFact child
documents in LanceDB. Repeated imports validate the committed prefix, resume
from the active Task Episode, and repair committed Markdown through an audited
recovery path:

```bash
uv run codecairn import /path/to/session.jsonl \
  --repo-key owner/repository \
  --root .codecairn
uv run codecairn list --repo-key owner/repository --root .codecairn
```

Production recall uses Alibaba Cloud Model Studio's OpenAI-compatible embedding
API with `text-embedding-v4` at 1,024 dimensions. CrossEncoder reranking
remains local through the pinned `Xenova/ms-marco-MiniLM-L-6-v2` ONNX artifact.
Configure a DashScope key before indexing or recall; offline commands such as
evidence verification do not require provider credentials. A workspace-specific
base URL can replace the public endpoint without changing durable Markdown truth:

```bash
export DASHSCOPE_API_KEY="<your-api-key>"
export CODECAIRN_EMBEDDING_MODEL="text-embedding-v4"
export CODECAIRN_EMBEDDING_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export CODECAIRN_EMBEDDING_DIMENSION="1024"

export CODECAIRN_MODEL_CACHE="$HOME/.cache/codecairn/models"
export CODECAIRN_RERANKER_MODEL="Xenova/ms-marco-MiniLM-L-6-v2"
export CODECAIRN_RERANKER_SOURCE="Xenova/ms-marco-MiniLM-L-6-v2"
export CODECAIRN_RERANKER_REVISION="a09144355adeed5f58c8ed011d209bf8ee5a1fec"
```

The API key is never written to index rows, recall sidecars, or evaluation
manifests. Changing the endpoint, model, declared provider revision, dimension,
or Adapter version re-embeds the disposable LanceDB projection under an
inter-process lock. The provider alias is recorded as `provider-managed`
because DashScope does not expose an immutable artifact commit for it.

For explicit offline operation, set `CODECAIRN_RETRIEVAL_PROFILE=fastembed` and
configure the pinned local embedding artifact variables from ADR 0013. The
deterministic hashing profile is test-only. Neither profile is a silent fallback
when DashScope is unavailable or unconfigured.

Runtime state is ignored by Git because it can contain source paths, commands,
and evidence text.

## Local CLI and HTTP

Install the package from a checkout, or run the same commands through `uv`:

```bash
uv tool install .
codecairn --help
codecairn recall "pytest command failed" \
  --repo-key owner/repository \
  --root .codecairn
codecairn doctor --root .codecairn
```

The evaluation command dispatches all three independent evidence suites plus
the recovery suite through the same application interface used by HTTP. Inputs
and output roots are explicit; every run identifier is immutable.

```bash
codecairn eval run retrieval benchmarks/retrieval \
  --run-id retrieval-<commit> \
  --repository-commit <commit> \
  --output-root artifacts
codecairn eval report retrieval artifacts/retrieval/retrieval-<commit>
```

Completed evaluation artifacts can be reduced to a public, immutable evidence
bundle without copying private runtime state. The build command generates the
metrics, English and Chinese resume copy, and a SHA-256 inventory. Verification
recomputes every report and generated document without provider credentials:

```bash
codecairn evidence verify evidence/benchmark-v1
```

The full artifact selection and benchmark interpretation rules are documented
in [docs/evidence-bundle.md](docs/evidence-bundle.md).

The HTTP server binds only to trusted loopback by default. It refuses a remote
bind and accepts import or evaluation inputs only below configured source
roots. Configure it without putting secrets on the command line:

```bash
export CODECAIRN_RUNTIME_ROOT="$PWD/.codecairn"
export CODECAIRN_ARTIFACT_ROOT="$PWD/artifacts"
export CODECAIRN_SOURCE_ROOTS="$PWD"
uv run codecairn-server
```

LoCoMo runs can use the legacy shared `CODECAIRN_OPENAI_*` settings or
independent `CODECAIRN_ANSWER_*` and `CODECAIRN_JUDGE_*` settings. For the
official DeepSeek endpoint, exporting only `DEEPSEEK_API_KEY` defaults both
roles to `deepseek-v4-pro` with thinking enabled; role-level model, endpoint,
key, profile, and reasoning-effort variables remain available for controlled
overrides. Health reports configuration state only and never emits credentials.
DeepSeek supplies LoCoMo answers and judge votes; DashScope supplies Qwen
embeddings and a pinned local model supplies CrossEncoder reranking. Run
manifests record all configurations separately.

Hierarchical recall defaults to `CODECAIRN_RECALL_MODE=hierarchy`. Reproducible
ablations may select `episode-only` or `hierarchy-no-neighbors`; the effective
mode and deterministic router contract are included in the retrieval manifest
and every query sidecar.

Resource-sensitive LoCoMo evidence runs reuse one verified corpus and one frozen
query-vector artifact, then isolate each conversation in a fresh worker process.
The v15 protocol first runs retrieval without answer or judge calls and reports
gold-evidence coverage. Paid scoring starts only after that provider-free gate.
A three-variant 40-question comparison selects one recall mode; only that mode
runs the 200-question diagnostic, whose absolute promotion gate is verified by
`codecairn eval promote-locomo`. Build the artifacts as documented in
`benchmarks/locomo/README.md`, then run. The v14 retrieval-only diagnostic
remains immutable historical evidence and failed its context-coverage and
latency gates; v15 does not claim an improvement until a new immutable run
passes verification. [ADR 0022](docs/adr/0022-v15-rebalances-fact-selection-and-context-budgeting.md)
records that boundary.

```bash
codecairn eval run locomo benchmarks/locomo/data/locomo10.json \
  --run-id <retrieval-run-id> --repository-commit <commit> --mode retrieval \
  --output-root benchmark_results \
  --question-set benchmarks/locomo/diagnostic-200-v15.json \
  --corpus <content-addressed-corpus> --query-vectors <frozen-query-vectors> \
  --max-workers 10

codecairn eval report-locomo-evidence \
  benchmark_results/locomo/<retrieval-run-id> \
  --dataset benchmarks/locomo/data/locomo10.json \
  --output benchmark_results/locomo/<retrieval-run-id>/evidence-coverage.json
```

The six versioned routes cover import, memory list, recall, evaluation run,
evaluation report, and health. Every error response has the same shape and an
`x-request-id` response header:

```json
{
  "error": {"code": "validation_error", "message": "Request validation failed"},
  "request_id": "..."
}
```

`doctor` and `/api/v1/health` report Markdown truth, Import Ledger counts,
queue lag, index parity/readiness, and provider configuration separately. They
never return provider credentials.

Project contracts live in [CONTEXT.md](CONTEXT.md),
[docs/architecture.md](docs/architecture.md), and [docs/adr/](docs/adr/).

## License

The repository will receive an explicit open-source license before its first
tagged release. Until then, no license is granted by default.
