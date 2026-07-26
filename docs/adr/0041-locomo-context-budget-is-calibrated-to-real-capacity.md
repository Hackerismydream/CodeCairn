# The LoCoMo Context Budget Is Calibrated to Real Capacity

## Status

Accepted. It amends the 4,000-token context ceiling that ADR 0023 and ADR 0026
held constant. The frozen v23 protocol files keep that ceiling and are
unchanged; the new budget is a new protocol identity.

## Context

Recall Context is bounded by `context_max_tokens` and `context_max_chars`, and
the counter is `codecairn/utf8-two-byte-upper-bound-v1`: two UTF-8 bytes count
as one token. That rule is a deliberate upper bound, chosen so the counted
token total can never understate the real one and the budget stays a hard cap
rather than an estimate.

The published v3 base run measured what the bound costs. Its aggregate context
diagnostics in `evidence/benchmark-v3/raw/locomo/sources/base/report.json`
average 7,916.01 rendered characters against 3,989.30 counted tokens: 1.98
characters per counted token. English chat text tokenizes at roughly 3.5 to 4
characters per real token, so a 4,000-token declared budget saturated at
roughly 2,000 real tokens — about half the capacity the protocol claimed to
hand the answer model.

The same run dropped an average of 161.73 candidate snippets and 5.06 selected
parents per question at the packing boundary, against 14.79 rendered parents.
Evidence that had already survived retrieval and ranking was being cut by the
budget.

ADR 0023 and ADR 0026 both refused to raise the ceiling, and the reason was
sound: a larger budget can hide a packing bug by making it cheap to include
everything, and the lightweight runtime objective does not tolerate an
unbounded context. That reasoning assumed the declared budget matched real
model capacity. It did not.

## Decision

The declared budget doubles. `RecallPlannerConfig.context_max_tokens` becomes
8,000 and `context_max_chars` becomes 47,800, so the real context reaching the
answer model roughly doubles while the counted-token guarantee is unchanged.

The upper-bound tokenizer stays. Its guarantee is what makes a doubled declared
budget a bounded claim rather than a hope about tokenizer behavior.

The packing-failure detector stays, and it is why the raise is safe. Every
scored run records the omitted parent identities and the omitted snippet count
for each question; the trace audit rejects a context whose rendered and omitted
sets do not partition the ranked evidence; and the per-run averages are
published. A packing regression is still visible as omitted evidence instead of
being absorbed by slack.

The change is a new frozen identity, not an edit. `diagnostic-200-v24.json`,
`diagnostic-200-v24-thinking.json`, and `full-1540-v24.json` carry the new
budget, and the retrieval spend gate reads its token ceiling from the protocol
it targets. The v23 files remain byte-identical, so every published v23 result
keeps the protocol that produced it.

## Consequences

- Answer input cost rises with the larger real context. The v24 protocol must
  be re-baselined before any of its numbers are read as an improvement.
- v23 and v24 scores are not one series. The protocol identity recorded in each
  manifest says which budget produced a score.
- The omitted-parent and omitted-snippet averages remain the packing detector.
  They are expected to fall, not to disappear; a value near zero would mean the
  budget no longer binds and the detector has stopped detecting.
- The 1.98 characters-per-counted-token ratio is a property of this corpus and
  this renderer. A different corpus needs its own calibration rather than a
  reuse of this constant.
