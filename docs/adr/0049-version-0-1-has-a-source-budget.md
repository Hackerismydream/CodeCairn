---
status: accepted
---

# Version 0.1 Has a Source Budget

The release is intended to be read and learned from, so source size is an
acceptance constraint rather than a retrospective metric. At the accepted
baseline `main@954f728`, `src/codecairn` contains 34,091 physical Python lines:
16,841 in evaluation and 17,250 elsewhere.

Version 0.1 must reduce the non-evaluation product core to at most 10,000
physical Python lines and the complete `src/codecairn` package to at most
15,000. Tests and documentation do not count. A checked-in deterministic
source-budget command enforces both ceilings.

The reduction removes obsolete Evidence Gate/type-specific paths and collapses
historical benchmark orchestration while retaining four public evaluation
commands and immutable historical evidence. Moving code to another installable
package or generated file does not satisfy the budget.
