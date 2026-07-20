# Public evidence bundle

CodeCairn publishes benchmark claims only through a generated evidence bundle.
The reducer treats saved suite summaries as assertions: it recomputes LoCoMo,
retrieval, recovery, and CodingMemoryBench reports from their raw JSON inputs
and rejects the build if any saved summary differs.

The only compatibility exception is a known historical LoCoMo category-label
mapping. The reducer may replace those labels when every numeric field and all
other report content exactly match the recomputed report. It then publishes a
`raw/locomo/amendment.json` record containing the source summary hash, each
label correction, and an explicit declaration that no numeric metric changed.
The original aggregate report is retained as `raw/locomo/source-summary.json`,
so offline verification can validate both its hash and the exact label-only
transformation against the fixed legacy and current mappings. Arbitrary report
drift is still rejected.

## Build contract

The build requires four completed immutable run directories plus JUnit and
coverage JSON from the same source checkout:

```bash
uv run codecairn evidence build \
  --bundle-id benchmark-v1 \
  --locomo-run /path/to/locomo-run \
  --retrieval-run /path/to/retrieval-run \
  --recovery-run /path/to/recovery-run \
  --coding-run /path/to/coding-run \
  --quality-junit /path/to/junit.xml \
  --quality-coverage /path/to/coverage.json \
  --generator-commit <commit> \
  --repository-root . \
  --output-root evidence
```

The output is exclusive: an existing bundle directory is never overwritten.
The reducer copies manifests, query records, recovery checks, normalized coding
traces, public verifier results, and normalized LoCoMo ingest/question
checkpoints. Public LoCoMo question records retain category, status, generated
answer, normalized judge labels, retry metadata, usage, and the original
artifact hash while excluding raw judge responses, the dataset question, gold
answer, evidence text, retrieval query, and recalled conversation content.
Public ingest records retain only identifiers, aggregate counts, and the
original artifact hash, excluding speaker names and runtime paths. Public
verifier records retain outcome, timing, output
hash, verifier-source hash, and the original artifact hash while excluding
machine-local paths and stderr. The bundle also excludes runtime databases,
vector indexes, final workspaces, provider secrets, and the LoCoMo dataset file.

## Verification contract

```bash
uv run codecairn evidence verify evidence/benchmark-v1
```

Verification requires no model provider or private trace. It checks the file
inventory, recomputes all suite reports and counts, and regenerates the README
and both resume documents in memory. CI runs this command after the normal lint,
type, import-boundary, and test gates.

Each headline claim in `metrics.json` and the generated README carries three
provenance fields:

- `manifest`: the immutable run identity and model/configuration record;
- `raw_inputs`: the records consumed by aggregation;
- `aggregation_command`: the public command that recomputes the claim.

The generated manifest also records the dependency-lock hash, source commits,
models, available cost observations, local environment, licenses, and known
limitations.

## Interpretation rules

- Category IDs follow the public LoCoMo evaluator: 1 is multi-hop, 2 is
  temporal, 3 is open-domain, 4 is single-hop, and 5 is adversarial. Full
  accuracy covers the selected answerable categories 1-4; adversarial questions
  are reported separately when selected rather than silently treated as
  answerable questions.
- LoCoMo smoke validates full-dataset ingestion and a small end-to-end question
  path. It is always unscored and must never be presented as LoCoMo accuracy.
- A full LoCoMo bundle publishes accuracy only when the question checkpoints
  cover every category 1-4 question declared by the selection manifest, every
  question has the configured valid judge-vote count, and infrastructure
  failures are zero. A judge vote retries malformed structured output up to the
  attempt limit recorded in the run manifest. The manifest also records the
  maximum accepted response length. All attempts remain in the raw checkpoint
  and count toward token and cost totals. Provider-native CNY cost remains
  distinct from USD cost.
- CodingMemoryBench compares memory-off and memory-on over the same 20 tasks,
  three repeats, isolated workspaces, and a verifier hidden from the agent.
- Retrieval reports measure the checked-in 100-query corpus on the recorded
  local environment. P95 latency is not a cross-machine service SLO.
- Provider cost remains pending when the raw provider artifact exposes no cost.
- Public fixtures and controlled coding tasks are not described as private or
  production user traces.

LoCoMo is sourced from the
[official repository](https://github.com/snap-research/locomo) and licensed
CC BY-NC 4.0. The dataset is not redistributed in the evidence bundle.
