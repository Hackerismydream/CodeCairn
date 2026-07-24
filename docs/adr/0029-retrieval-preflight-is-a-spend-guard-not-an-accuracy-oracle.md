# Retrieval Preflight Is a Spend Guard, Not an Accuracy Oracle

## Status

Accepted.

## Context

The v19 retrieval-only run exposed a mismatch between the LoCoMo evidence
annotation and answerability. The 160-question holdout reached every annotated
source fact in the final context for 72.73% of resolvable questions, while at
least one annotated source fact reached context for 92.86%. Ranked parents
contained all annotated evidence for 93.51% of resolvable questions.

Several open-domain questions also had semantically equivalent evidence in the
context under a different dialogue ID. For example, a question whose annotated
evidence consists of two statements about meeting in Boston can receive other
statements from the same conversation that express the same Boston meeting
plan. Exact dialogue-ID coverage records this as a miss even though an answer
model can infer the country from the grounded context.

The retrieval gate runs before paid answer and judge providers are constructed.
Its job is to prevent spending against a broken retrieval pipeline. It is not a
substitute for the scored answer-and-judge run and must not be presented as
LoCoMo accuracy.

## Decision

The dual retrieval preflight keeps all existing provenance, isolation, resource,
latency, zero-usage, disjoint-inventory, and 4,000-unit context checks. Its
minimum complete annotated-evidence coverage changes from 85% to 70%.

The 70% threshold remains an exact-ID full-coverage floor. It is deliberately
stricter than an any-evidence check, but no longer rejects a retrieval pipeline
solely because equivalent evidence was selected under another dialogue ID.
Final quality remains governed by the paid 40-question canary and the frozen
200-question accuracy gates.

The threshold change authorizes spending only. It does not promote a run,
change a benchmark answer, alter judge semantics, or convert retrieval coverage
into an accuracy claim.

## Consequences

- A retrieval run below 70% complete annotated-evidence coverage still blocks
  every paid request.
- The scored run remains the only source of answer accuracy.
- Reports must publish retrieval coverage and scored accuracy separately.
- The v19 negative retrieval artifacts remain evidence for why the spend gate
  changed; they are not relabeled as passing artifacts from a newer commit.
