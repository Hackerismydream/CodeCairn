---
status: accepted
---

# Version 0.2 Has an Additive Pico Integration Budget

## Context

ADR 0049 made source size an acceptance constraint for version 0.1. The final
version 0.1 baseline is 9,700 non-evaluation Python lines and 14,100 total
package lines. Version 0.2 adds a CodeCairn-owned Pico Source Journal, importer,
and installed Memory Backend adapter. Pretending that this new integration has
no source cost would either make the accepted work impossible or encourage
moving policy outside the counted package.

The budget must remain a public architectural decision. A delivery task may
not make `make check` green by silently replacing the maintained ceiling.

## Decision

Keep all version 0.1 stages and the `release` stage unchanged. Add two
post-v0.1 delivery stages:

| Stage | Non-evaluation core | Complete package | Owned scope |
|---|---:|---:|---|
| `v02-001` | 10,550 | 14,850 | Pico Source Journal and evidence-preserving importer |
| `v02-002` | 11,000 | 15,300 | Installed Pico Memory Backend adapter and packaging |

The evaluation tree receives no additional allowance; each total ceiling is
the core ceiling plus the unchanged evaluation baseline, rounded only to keep
the gate legible. Tests and documentation remain outside the count.

`make check` selects the stage corresponding to the implementation state on
`main`. The report must expose the same stage limit as its internal target.
The `release` stage continues to mean the frozen version 0.1 package and is not
relabelled as version 0.2.

These are ceilings, not allocation targets. Moving code to another installable
package or generated file still does not satisfy the budget.

## Consequences

- version 0.1 evidence and its source gate remain reproducible;
- each Pico delivery has a deterministic, reviewable size gate;
- the v0.2 integration may add at most 1,300 core lines across both serial
  tasks;
- later v0.2 work must add a new decision instead of increasing these values
  inside the checker.
