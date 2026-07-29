# CodeCairn 0.1 Release Candidate

This candidate is bound to implementation commit
`f2358a77696f38283a237d9be67ec514885aff76`.

## Verified outcomes

- LoCoMo diagnostic: 153/200 raw accuracy, 84.88% natural-category-weighted
  accuracy, zero infrastructure failures, and 4.91-second retrieval P95.
- LoCoMo full: 1,264/1,540 correct (82.08%) after an exact one-question
  infrastructure repair, with zero final infrastructure failures and
  4.87-second retrieval P95.
- CodingMemoryBench-20: 120 isolated Codex runs. Memory-off passed 48/60
  (80.0%); memory-on passed 60/60 (100.0%), a 20 percentage-point increase,
  with 12 paired improvements and zero memory-induced regressions.
- Retrieval: 97.0% Recall@5 over 100 queries, 100% provenance coverage,
  zero stale-predecessor leakage, and 39.48-millisecond P95.
- Scale: 1,000 sessions and 100,000 events produced 1,000 unique Episodes and
  1,000 Memories; repeated import created zero additional records.
- Recovery: all eight release-critical write-intent crash boundaries passed.
- Real Codex and Claude Code hooks both passed native trigger, receipt,
  idempotency, and recall verification with isolated configuration.
- Product source: 9,700 core Python lines and 13,978 total package Python
  lines.

## Evidence boundaries

- The LoCoMo answer and judge aliases are provider-managed rather than pinned
  model revisions.
- CodingMemoryBench supplies checked-in pre-retrieved context; it demonstrates
  controlled context-use impact, not end-to-end retrieval quality.
- The real Claude smoke used DeepSeek's Anthropic-compatible endpoint under an
  isolated provider environment because the local Claude OAuth session was
  expired.
- Raven integration remains outside version 0.1.
