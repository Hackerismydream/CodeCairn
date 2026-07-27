---
status: accepted
---

# Version 0.1 Evolves Memory Through Supersession

Version 0.1 implements one real Evolution Layer operation. The model may
automatically propose `keep_both` or `supersede`; CodeCairn applies
Supersession only after validating identities, namespace, eligible types,
lifecycle state, and absence of self-reference or cycles. It does not require
evidence verification or human approval.

Task Experience is append-only. A newer Work State for the same Workstream
always supersedes the previous state; a newer explicit User Preference may
supersede the previous preference on the same subject; Repository Knowledge
supersedes only a same-subject item judged obsolete or contradictory.

Each applied relation is an immutable Evolution Record in Markdown Truth.
SQLite and LanceDB project the resulting active status. Restore creates a new
memory revision derived from the historical content and may supersede the
current revision; it never mutates or reverses history.
