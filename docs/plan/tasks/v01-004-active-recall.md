---
id: v01-004
scope: lifecycle-aware retrieval and context rendering
status: ready
depends-on: [v01-003]
---

# Make recall active-only, typed, and bounded

## Objective

Compile useful Recall Context from active memory while preserving explicit
historical access and reducing the current retrieval mode matrix.

## Context

The baseline `service/recall.py` and `memory/recall_planner.py` together exceed
3,700 lines and encode historical experiment paths. Version 0.1 needs one
default product route plus explicit history.

## Paths

Primary:

- `src/codecairn/service/recall.py`
- `src/codecairn/memory/recall_planner.py`
- `src/codecairn/memory/context.py`
- `src/codecairn/memory/projection.py`
- `src/codecairn/storage/lance.py`
- `src/codecairn/service/cascade.py`
- `src/codecairn/service/application.py`
- `tests/test_recall.py`
- `tests/test_recall_context_budget.py`
- `tests/test_recall_planner.py`
- `tests/test_mini_cascade.py`

## Required changes

1. Add namespace and `status=active` to candidate filtering before context
   compilation.
2. Support `include_superseded=false` by default and an explicit historical
   query flag.
3. Identify and pin at most one matching active, open Work State.
4. Rank closed Work State plus remaining Repository Knowledge, User Preference,
   and Task Experience through the retained lexical/vector/reranker path.
5. Apply one total token budget and versioned per-type caps after ranking.
6. Render attributed Markdown and a sidecar with ID, type, status, score,
   source references, retrieval profile, selected/omitted reason, and budget.
7. Remove product routing branches and representation modes not required by
   the release protocol or historical evidence verification.
8. Rebuild indexes both active and historical documents with status so a
   historical query does not require Markdown scanning.
9. Keep the four-second local P95 release SLO measurable; do not assert it from
   unit timing.

## Deterministic selection order

For equal scores:

1. pinned open Work State;
2. memory-type priority: Repository Knowledge, User Preference, closed Work
   State, Task Experience;
3. newer `created_at_ms`;
4. lexical `memory_id`.

The sidecar records the effective caps and every budget omission.

## Verification

```bash
uv run pytest \
  tests/test_recall.py \
  tests/test_recall_context_budget.py \
  tests/test_recall_planner.py \
  tests/test_mini_cascade.py \
  tests/test_projection_cache.py
make format
make check
```

Required cases:

- superseded item is absent by default and present explicitly;
- matching open Work State is pinned but still bounded;
- closed Work State is not pinned;
- no Work State, one Work State, parallel workstreams;
- each memory type gets its cap and deterministic ties;
- missing/stale index is an error, not Markdown fallback;
- sidecar explains omissions and carries current retrieval identity;
- rebuild parity covers statuses and child documents.

## Exit criteria

- normal recall is active-only;
- historical access is explicit and attributed;
- one documented selection path replaces historical product modes;
- `service/recall.py` plus planner shrink materially;
- all checks pass and line deltas are reported.
