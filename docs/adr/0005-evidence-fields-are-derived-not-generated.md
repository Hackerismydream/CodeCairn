# Evidence Fields Are Derived, Not Generated

Deterministic collectors derive role, quote, command, exit status, changed file,
and verification facts from Agent Trace events. An LLM may propose a type,
summary, guidance, and relationships between supplied fact identifiers.

The Evidence Gate resolves those identifiers back to facts and validates each
memory type. It rejects invented quotes, role changes, failed verification, and
claims whose required facts are missing.
