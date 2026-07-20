# Evidence bundle: benchmark-v2

This directory is generated from immutable evaluation artifacts. Do not edit its
metrics or recruiting copy by hand; rebuild it with the command in the manifest.

## Headline measurements

| Measurement | Value | Manifest | Raw inputs | Aggregation |
|---|---:|---|---|---|
| Retrieval Recall@5 | 96.00% | [raw/retrieval/manifest.json](raw/retrieval/manifest.json) | [`raw/retrieval/queries/*.json`](raw/retrieval/queries) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Retrieval MRR | 0.7979 | [raw/retrieval/manifest.json](raw/retrieval/manifest.json) | [`raw/retrieval/queries/*.json`](raw/retrieval/queries) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Retrieval P95 latency | 10.91 ms | [raw/retrieval/manifest.json](raw/retrieval/manifest.json) | [`raw/retrieval/queries/*.json`](raw/retrieval/queries) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Index rebuild consistency | 100.00% | [raw/recovery/manifest.json](raw/recovery/manifest.json) | [`raw/recovery/checks.json`](raw/recovery/checks.json) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Coding task pass rate, memory off | 85.00% | [raw/coding/experiment.json](raw/coding/experiment.json) | [`raw/coding/*/result.json`](raw/coding) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Coding task pass rate, memory on | 100.00% | [raw/coding/experiment.json](raw/coding/experiment.json) | [`raw/coding/*/result.json`](raw/coding) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Coding task pass-rate change | 15.00 pp | [raw/coding/experiment.json](raw/coding/experiment.json) | [`raw/coding/*/result.json`](raw/coding) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Coding total-token reduction | 2.26% | [raw/coding/experiment.json](raw/coding/experiment.json) | [`raw/coding/*/result.json`](raw/coding) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Steps-to-first-useful-action reduction | 3.41% | [raw/coding/experiment.json](raw/coding/experiment.json) | [`raw/coding/*/result.json`](raw/coding) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Official LoCoMo sessions ingested | 272 sessions | [raw/locomo/manifest.json](raw/locomo/manifest.json) | [`raw/locomo/checkpoints/ingest/*.json`](raw/locomo/checkpoints/ingest) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| LoCoMo full completion | 100.00% | [raw/locomo/manifest.json](raw/locomo/manifest.json) | [`raw/locomo/checkpoints/questions/*/*.json`](raw/locomo/checkpoints/questions) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| LoCoMo answer accuracy | 47.73% | [raw/locomo/manifest.json](raw/locomo/manifest.json) | [`raw/locomo/checkpoints/questions/*/*.json`](raw/locomo/checkpoints/questions) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Automated tests | 171 tests | [bundle-manifest.json](bundle-manifest.json) | [`raw/quality/junit.xml`](raw/quality/junit.xml) | `uv run codecairn evidence verify evidence/benchmark-v2` |
| Statement coverage | 83.53% | [bundle-manifest.json](bundle-manifest.json) | [`raw/quality/coverage.json`](raw/quality/coverage.json) | `uv run codecairn evidence verify evidence/benchmark-v2` |

## Artifact-derived scale

- LoCoMo: 10 conversations, 272 sessions, 5882 turns, 5882 accepted memories, and 0 rejected memories.
- Retrieval: 100 isolated queries.
- Coding A/B: 120 runs, 1298 normalized events, 536 command/tool calls, 123 file changes, and 120 hidden-verifier results.

## Pending measurements

- **CodingMemoryBench provider cost** — pending: The provider trace contains no cost observations.

## Known limitations

- LoCoMo category names were corrected from numeric category identifiers in a label-only report amendment; scores, votes, usage, and source hashes are unchanged.
- LoCoMo category 5 is adversarial and excluded from the official scored subset.
- Provider cost is pending where upstream artifacts expose no cost observation.
- Coding tasks and public fixtures are controlled evaluations, not private production traces.
- Latency was measured on one local machine and is not a cross-machine guarantee.
- An earlier CodingMemoryBench v1 run was invalidated and excluded after a verifier defect was found.

## Reproduce

```bash
uv run codecairn evidence verify evidence/benchmark-v2
```

The verifier recomputes all four suite reports, aggregate counts, recruiting
copy, and the SHA-256 inventory. It requires no private trace or provider key.

LoCoMo is attributed to the [official repository](https://github.com/snap-research/locomo)
and is licensed CC BY-NC 4.0. The dataset file is not redistributed here.
