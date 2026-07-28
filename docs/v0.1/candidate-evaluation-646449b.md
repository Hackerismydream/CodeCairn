# Candidate Evaluation: `646449b`

Status: **not releasable**. The 200-question diagnostic passed, but the full
LoCoMo run crossed the mathematical failure boundary before completion. Coding
A/B was therefore not started.

The machine-readable count evidence is
[`../../benchmarks/locomo/candidate-646449b-summary.json`](../../benchmarks/locomo/candidate-646449b-summary.json).
Protected LoCoMo question and answer text remains machine-local and is not
redistributed.

## Verified current-SHA results

| Suite | Result |
|---|---|
| Quality | 188 tests; Ruff, Mypy, import contracts, and source budget pass |
| Source budget | 9,700 core / 4,278 evaluation / 13,978 total Python lines |
| Lifecycle smoke | 2 client fixture families, 204 triggers, 100% read-your-writes, 100% continuation, 0 duplicate memories |
| Scale | 1,000 sessions, 100,000 raw events, 1,000 Episodes and memories, 0 duplicate Episodes, 0 repeat-created memories, 75.825 seconds |
| Retrieval | 100 queries, Recall@5 97%, provenance 100%, stale predecessor leakage 0%, P95 52.89 ms |
| LoCoMo-200 | 155/200 raw; 85.58% natural-weighted; 0 infrastructure failures; retrieval P95 3.10 seconds; promotion pass |

The offline aggregate artifacts are machine-local under
`/private/tmp/codecairn-v01-rc14-646449b/offline/`. Their aggregate SHA-256
values are:

- lifecycle smoke:
  `d5e4383edeedf306bc8b671ecbfdf3261a700499b9dccc793ad8bf0d5021c8ab`;
- scale:
  `8eef50923cd7a7556477929aac0e0df1f2814d4a775d66f3db2af3b3a542f0e7`;
- retrieval:
  `b71c2cc4f667cf3e5c0009aa0bfac773f636d58c078ddbff0c25d8b9ff8fbd05`.

## Full-run failure boundary

The full run stopped after 1,439 of 1,540 questions:

- 1,157 correct, 282 wrong, and 0 infrastructure failures;
- observed partial accuracy 80.40%;
- retrieval P95 5.41 seconds;
- category partials: 65.58%, 81.15%, 59.38%, and 88.20%;
- even if all 101 unscored questions were correct, final accuracy could reach
  only 81.69%;
- the release minimum permits at most 277 wrong answers, so 282 made 82%
  mathematically impossible.

This is a failed candidate, not an 80.40% published LoCoMo result. The
1,540-question release gate remains unsatisfied.

## Failure attribution

A provider-free sample of 100 wrong answers, balanced at 25 per category,
showed that Recall Context contained at least half the reference-answer tokens
for 77 questions and at least 80% for 49. Exact normalized reference text was
present for 17. The judge was unanimous on 253 of 282 wrong answers, so judge
vote instability was not the main cause.

Increasing per-memory excerpt candidates from 12 to 20 and global compilation
candidates from 192 to 256 changed none of the sampled coverage counts because
the existing token budget was already binding. The experiment was reverted.

A paired provider check on 62 diagnostic questions also found no benefit from
using DeepSeek V4-Pro for answers: Flash scored 49 and Pro scored 48. Pro fixed
two Flash errors but introduced three regressions. That experiment was stopped
and did not change the release protocol.

## Resume-safe project wording

Until a complete candidate passes the full gate, external project text may
claim the implemented and verified lifecycle, scale, retrieval, and evaluation
system. It must not claim a current 82%+ full LoCoMo score, Coding A/B
improvement, or version 0.1 release.

Recommended concise wording:

> Built a local-first, auditable long-term memory runtime for coding agents
> with Markdown truth, SQLite recovery state, rebuildable hybrid retrieval,
> MCP, and Codex/Claude Code hooks. Verified duplicate-free import across
> 1,000 sessions and 100,000 events, 97% Recall@5 with 100% provenance
> coverage and 52.89 ms local P95 across 100 queries, and a reproducible
> LoCoMo/Coding A/B evaluation pipeline with immutable manifests and
> fail-closed spend gates.

The wording intentionally describes the evaluation pipeline rather than a
full LoCoMo score that the current candidate did not achieve.
