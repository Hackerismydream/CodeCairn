# Candidate Evaluation: `f2358a7`

Status: **release gates pass; preferred LoCoMo ship band not reached**.

The offline-verifiable release evidence is
[`../../evidence/v0.1-rc1/metrics.json`](../../evidence/v0.1-rc1/metrics.json).
It binds every result below to implementation commit
`f2358a77696f38283a237d9be67ec514885aff76`. Protected LoCoMo question,
answer, and provider-attempt content is not redistributed.

## Verified candidate results

| Suite | Result |
|---|---|
| Quality | Ruff, formatting, Mypy, import contracts, tests, docs, and artifact checks pass |
| Source budget | 9,700 core / 4,278 evaluation / 13,978 total Python lines |
| Lifecycle smoke | 2 client fixture families, 204 triggers, 100% read-your-writes, 100% continuation, 0 duplicate memories |
| Scale | 1,000 sessions, 100,000 raw events, 1,000 Episodes and Memories, 0 duplicate Episodes, 0 repeat-created memories, 55.03 seconds |
| Recovery | 8 release-critical Write Intent crash boundaries, 100% pass |
| Retrieval | 100 queries, Recall@5 97%, provenance 100%, stale predecessor leakage 0%, P95 39.48 ms |
| LoCoMo-200 | 153/200 raw; 84.88% natural-category-weighted; 0 infrastructure failures; retrieval P95 4.91 seconds; promotion pass |
| LoCoMo-1540 | 1,264/1,540, 82.08%; 0 final infrastructure failures; retrieval P95 4.87 seconds |
| CodingMemoryBench-20 | 120 isolated Codex runs; memory-off 48/60 (80%); memory-on 60/60 (100%); +20 percentage points; 12 paired improvements; 0 regressions |
| Installed artifact | First install-to-manual-recall path 17.10 seconds; one-client path 20.02 seconds |
| Real clients | Codex and Claude Code native hook, receipt, repeat-idempotency, and recall checks pass |
| Packaging | Wheel and sdist are byte-identical across two clean builds |

## LoCoMo interpretation

The full base run retained one infrastructure failure. A separate immutable
repair run selected exactly that one failed question, kept the protocol,
dataset, retrieval, answer, and judge identities unchanged, and scored it
correct. The composed result is:

- category 1 multi-hop: 191/282, 67.73%;
- category 2 temporal: 259/321, 80.69%;
- category 3 open-domain: 62/96, 64.58%;
- category 4 single-hop: 752/841, 89.42%;
- total: 1,264/1,540, 82.08%.

This clears the frozen 82% release minimum by 0.08 percentage points. It does
not reach the preferred 85% to 86% optimization-stop band, so 82.08% is the
only candidate score that may be claimed.

## Coding A/B interpretation

The same 20 repair tasks ran three times per arm in isolated workspaces with a
hidden verifier. The memory-on arm received only checked-in pre-retrieved
Recall Context. It changed 12 paired outcomes from failed to passed and changed
zero passed outcomes to failed.

This demonstrates controlled context-use value. It does not, by itself,
demonstrate that live transcript import and retrieval will select the same
context for arbitrary repositories; the lifecycle, retrieval, and real-client
suites cover those boundaries separately.

## Resume-safe project wording

Recommended concise wording:

> Built CodeCairn, a local-first auditable long-term memory runtime for Codex
> and Claude Code, using Python, MCP, Markdown durable truth, SQLite recovery
> state, LanceDB hybrid retrieval, and session-end hooks. Implemented
> crash-recoverable incremental trace import, four typed memory records,
> immutable supersession/restore, and provenance-aware recall. Verified
> duplicate-free import across 1,000 sessions and 100,000 events, 97%
> Recall@5 with 100% provenance coverage and 39.48 ms P95 over 100 queries,
> 82.08% on 1,540 LoCoMo questions, and a 20-point CodingMemoryBench pass-rate
> increase from 80% to 100% across 120 isolated Codex runs with zero
> memory-induced regressions.

Recommended interview boundary:

> LoCoMo uses provider-managed Qwen embedding and DeepSeek answer/judge aliases,
> so the manifest makes that mutability explicit. CodingMemoryBench measures
> the effect of controlled pre-retrieved context; it is not presented as an
> end-to-end retrieval benchmark. Raven integration is post-v0.1.
