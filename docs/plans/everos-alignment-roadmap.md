# EverOS Alignment Roadmap

Status: historical and partially completed. Fable's B0/P0 work was merged into
`main@954f728`. The remaining product program was absorbed and superseded by
the accepted version 0.1 PRD, architecture, and agent-ready tasks under
`docs/v0.1/` and `docs/plan/`. This file preserves the reasoning that produced
the baseline; it is no longer the delivery authority.

CodeCairn's reference project is EverOS (`EverMind-AI/EverOS`, local checkout
expected at `~/code/EverOS`). "Alignment" means two separate programs that share
almost no code:

1. **Benchmark program** — reach an honest 86–88% on the full 1,540-question
   LoCoMo set under CodeCairn's own protocol, then ~90% with structural
   retrieval work, and publish one same-harness comparison against a local
   EverOS run.
2. **Product program** — close the usability gap: today the runtime is
   CLI/HTTP-only and two verified defects make the happy path return nothing.

## Where the numbers stand (verified 2026-07-26)

| Fact | Value | Source |
|---|---|---|
| CodeCairn LoCoMo full run | 82.60% (1272/1540) | `evidence/benchmark-v3/raw/locomo/summary.json` |
| Per category (MH/T/OD/SH) | 70.21 / 87.23 / 59.38 / 87.63 | same |
| Answer+judge model | `deepseek-v4-flash`, thinking disabled, 3 votes | same manifest |
| Total provider cost | CNY 6.31 | same |
| Balanced-category macro average | 76.11% | recomputed from per-category |
| EverOS paper headline | 93.05% (GPT-4.1-mini answerer, agentic retrieval) | arXiv:2601.02163 Table 1 |
| EverOS with GPT-4o-mini answerer | 86.76% | same, Table 1 |
| Third-party reproduction of 93.05 | none; issue #73 reproduced 38–52% and was closed without a recipe | github EverMind-AI/EverOS#73 |
| LoCoMo answer-key error rate | ~6.4%; judge accepts ~63% of topically-adjacent wrong answers | Penfield Labs audit |

Consequences adopted by this roadmap:

- Do not chase 93.05. That number is answer-model substitution plus an
  unreproduced agentic loop measured against a partly-broken answer key.
- CodeCairn's differentiator is that every published score is recomputable via
  `codecairn evidence verify`. Keep that property through every change below.
- Cross-system claims require one fixed harness running both systems; paper
  numbers are not comparable to ours (different answerer, judge, context
  budget, failure accounting, and EverOS's own harness mislabels categories —
  its `tests/test_locomo.py:176-181` maps 1=single-hop where the dataset's
  category 1 is multi-hop).

## Benchmark program

| Phase | Content | Expected |
|---|---|---|
| B0 (this milestone) | Fix measurement distortions: context budget under-fill, stratified-vs-natural gate weighting, protocol prep for a thinking-enabled arm. See `milestone-locomo-measurement.md` | measurement correctness; unlocks trustworthy iteration |
| B1 | Paid re-baselines: 200-question diagnostic at HEAD, thinking-enabled arm, third-party judge arm, own full-context ceiling, 3-repeat variance | 85–88% |
| B2 | Structural: bounded agentic second retrieval round (needs an ADR amending `query_time_llm_calls: 0` into a recorded mode), reranker upgrade (`gte-rerank-v2` via existing `RerankingProvider` seam), conversation-level rollup layer | ~90% |
| B3 | Same-harness EverOS comparison, Tier A (`hybrid`, no query-time LLM on either side) and Tier B (`agentic`, gap-sizing only). Patch EverOS's category labels and pin `--judge-runs 3` before reading its output | the publishable alignment claim |

High-value fixes deliberately rejected as judge-overfitting: rewarding
inference over abstention on open-domain, and answer-first phrasing rules.
Both buy points only because the scored subset excludes all 446 adversarial
questions. If ever attempted, they must be gated on a category-5 regression
run.

## Product program

Historical table below. P0 is complete. P1–P6 were re-scoped into v01-001
through v01-010; P4 dashboard and the watcher portion of P3 are deferred.

| Step | Content | Spec |
|---|---|---|
| P0 (this milestone) | Index maintenance surface + import drains by default; lazy provider construction with actionable errors. Fixes the two verified blockers (recall always empty; nothing runs without a DashScope key) | `milestone-usability-p0.md` |
| P1 | `codecairn init` wizard, human-readable `doctor`, `repos` / `memory show` / `prefetch`, config-file support | future |
| P2 | MCP server exposing recall/list/import/doctor plus `codecairn://memory/{id}` resources | future, needs ADR (explicit audited tool surface vs. "live hooks" exclusion) |
| P3 | Claude Code / Codex session-end auto-import hooks; `codecairn watch --once` backfill | future, needs ADR |
| P4 | Read-only evidence dashboard with raw-JSONL hash verification drill-down | future, needs ADR (read-only viewer vs. "dashboard UI" exclusion) |
| P5 | Wire `SemanticCompression` into `import_session` so imports produce more than Failed Command memories | future, needs provider-boundary design |
| P6 | License decision, PyPI publish, `uvx codecairn` | future |

## Working agreements for these milestones

- Every contract change ships as a new versioned artifact plus an ADR; never
  mutate a frozen protocol file.
- No paid benchmark runs inside a code milestone. Milestones end with the exact
  commands the maintainer runs to spend money deliberately.
- `make format` and `make check` green on every commit.
