# GPT Pro Review Brief

Review the CodeCairn version 0.1 pre-development package as a principal
engineer and product architect. This is a design and execution-readiness review,
not a request to implement code.

## Repository state

- Branch: local `main`
- Fable baseline: `954f728`
- Product: an independent local-first Memory OS for agents
- Version 0.1 client scope: Codex and Claude Code
- Explicit exclusion: Raven integration is post-v0.1
- Historical evidence: `evidence/benchmark-v3` reports 82.60% under its frozen
  pre-v0.1 protocol and must not be attributed to the new architecture

Read in this order:

1. `CONTEXT.md`
2. `docs/PRD.md`
3. `docs/architecture.md`
4. `docs/v0.1/README.md`
5. `docs/v0.1/memory-lifecycle.md`
6. `docs/v0.1/agent-integration.md`
7. `docs/v0.1/onboarding-and-operations.md`
8. `docs/v0.1/evaluation-and-release.md`
9. `docs/plan/analysis/v0.1-delivery.md`
10. `docs/plan/README.md`
11. every file under `docs/plan/tasks/`
12. ADRs 0043–0050

Use current source only to verify feasibility and implementation deltas. Treat
`docs/runtime/operations.md` as current behavior and the version 0.1 documents
as accepted target behavior.

## Decisions already accepted

- Five layers: Source, Experience, Knowledge, Evolution, Recall.
- Four memory types: Task Experience, Repository Knowledge, User Preference,
  Work State.
- Exactly one Task Experience per Task Episode.
- Storage does not require an Evidence Gate; the system still owns provenance,
  role, exact quote, command/file outcome, and verification fields.
- Model-proposed `keep_both`/`supersede` with structural/type validation.
- Immutable Evolution Records and forward-only Restore.
- Default active-only recall; matching open Work State pinned within a bounded
  context.
- CLI, MCP, and Claude/Codex session-end hooks are primary; HTTP is
  compatibility only.
- No watcher, hidden prompt injection, dashboard, cloud service, dynamic
  profiles, Raven, or formal EverOS comparison in v0.1.
- Source ceilings: at most 10,000 non-evaluation Python lines and 15,000 total,
  from a 34,091-line baseline.
- Release requires a new candidate-bound full LoCoMo result, not reuse of
  historical 82.60%.

Challenge internal contradictions and feasibility, but do not reopen a decision
only because another architecture is more familiar.

## Questions

1. Is the product subject consistently “Memory OS used by agents,” or does any
   contract accidentally make CodeCairn a coding-agent wrapper?
2. Are the five layers and four durable types complete enough for v0.1 without
   collapsing Source, Experience, Knowledge, Evolution, and Recall authorities?
3. Are Episode finalization/continuation, capture cardinality, User Preference
   source-role rules, Workstream identity, Supersession, cycle prevention, and
   Restore deterministic enough for two independent code agents to implement
   the same behavior?
4. Can Markdown remain durable truth while SQLite projects active status and
   queues? Identify any missing atomicity, rebuild, or failure contract.
5. Are the MCP schemas and hook semantics small, explicit, idempotent, and
   realistically compatible with current Codex/Claude behavior?
6. Does provider absence preserve useful deterministic memory without becoming
   a silent fallback or false semantic success?
7. Is reducing 34,091 lines to 15,000 credible under the proposed keep/delete
   map? Name exact modules or behaviors that would block the budget.
8. Do tasks have correct dependency order, non-overlapping completion
   boundaries, exact paths, and sufficient black-box verification for a code
   agent to start without product questions?
9. Do the one-command evaluation and release gates distinguish fixture,
   historical artifact, infrastructure failure, live run, and
   verifier-backed success?
10. Which P0/P1 omissions would cause rework after implementation begins?

## Required response

Return:

1. verdict: `GO`, `CONDITIONAL GO`, or `NO-GO`;
2. a short architecture summary in your own words;
3. findings grouped as P0 blocking, P1 pre-implementation, and P2 improvement;
4. for every finding: exact file/section, contradiction or failure mode,
   concrete recommended edit, and affected task IDs;
5. dependency/ownership problems as a table;
6. source-budget feasibility with a proposed per-area target;
7. missing acceptance tests;
8. a final list of document edits required before the first code task starts.

Do not provide generic best practices. If there is no blocking issue, state
that explicitly and identify `v01-001` as the next executable task.
