# Evidence Fields Are Derived, Not Generated

Deterministic collectors derive role, quote, command, exit status, changed file,
and verification facts from Agent Trace events. An LLM may propose a type,
summary, guidance, and relationships between supplied fact identifiers.

The Evidence Gate resolves those identifiers back to facts and validates each
memory type. It rejects invented quotes, role changes, failed verification, and
claims whose required facts are missing.

User Preference requires an exact substring of a user-authored fact and cannot
change the source role. Repository Convention requires a user-authored fact, a
configured repository rule document, or the same observation at two distinct
trace locations. Repository documents are hashed into deterministic fact and
evidence identities before compression.

The remote compression boundary serializes only a bounded, redacted fact
payload. Proposal output has a closed schema and may author title, summary,
quote, role claim, and fact references only. Gate audit rows retain the full
proposal fields, proposed and resolved fact identifiers, the decision reason,
and an accepted memory identifier when one exists.
