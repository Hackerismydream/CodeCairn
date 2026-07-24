# Public Evidence Publishes Exact-Repair Outcomes

## Status

Accepted.

## Context

ADR 0037 permits a formal LoCoMo score to combine an immutable full run with an
exact repair of only its infrastructure failures. The existing public evidence
reducer accepted one run directory and copied normalized question checkpoints.
It could not publish the composite without either trusting its aggregate JSON
or redistributing private answer and recall traces from both source runs.

The release claim must remain reproducible without provider credentials, the
licensed LoCoMo dataset, machine-local indexes, or private memory context.

## Decision

The evidence builder accepts either an ordinary LoCoMo run directory or a
formal exact-repair composite JSON. For a composite build it:

1. locates both source runs and the frozen target and repair selections;
2. rebuilds the composite through the ordinary exact-repair verifier and
   rejects any value-level difference;
3. publishes source manifest and report receipts plus privacy-safe per-question
   outcomes containing only identity, category, outcome, and source artifact
   digest;
4. publishes one final outcome per target question with an explicit `base` or
   `repair` source;
5. copies only aggregate ingest records needed to prove dataset scale.

Offline bundle verification then recomputes both source reports from public
outcomes, proves that repair IDs exactly equal the base failure set, proves that
every final outcome is unchanged from its named source, and recomputes the
overall score, category breakdown, and provider usage.

The public composite contract is versioned as
`public-exact-repair-outcomes-v1`. Existing ordinary-run bundles retain their
current format and verifier path.

## Consequences

- The formal repaired score is independently reproducible without another
  paid model run.
- The original infrastructure failures remain visible and cannot be erased by
  publishing only the repaired aggregate.
- Public artifacts disclose no LoCoMo question, answer, evidence text, recalled
  memory, or raw model response.
- A changed receipt, selection, repair inventory, source outcome, final outcome,
  category aggregate, or usage total fails closed.
- The evidence bundle contains more small outcome files, trading repository
  size for claim-level auditability.
