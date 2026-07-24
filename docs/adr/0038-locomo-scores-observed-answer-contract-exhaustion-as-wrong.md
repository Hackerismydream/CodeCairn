# LoCoMo Scores Observed Answer Contract Exhaustion as Wrong

## Status

Accepted.

## Context

A LoCoMo answer provider can return a complete, billable response that fails the
grounded-answer contract. After the bounded application retry is exhausted, the
checkpoint historically used the generic `infrastructure_failed` status because
no answer was safe to send to the judge.

That status is appropriate for provider failures, unknown spend, interrupted
workers, and malformed judge responses. It is not appropriate when every paid
answer call completed and the model output itself violated the benchmark answer
contract. Excluding such questions would overstate model quality and invite
rerun-based score selection.

## Decision

For a full LoCoMo run, CodeCairn scores an answer-contract exhaustion as an
incorrect answer when all of the following are verified:

- the failure occurred in the answer phase;
- the bounded retry receipt ended as `contract_exhausted`;
- every application attempt returned a response and was rejected by the local
  grounded-answer contract;
- the call and response counts equal the frozen maximum attempt count;
- the terminal error type matches the checkpoint.

The question contributes `false` to its category and the overall denominator.
It does not increment `completed_question_count`, because no answer reached the
judge, and it does not increment `infrastructure_failed_count`.

Provider failures, unknown-spend outcomes, retrieval failures, interrupted
workers, and judge failures remain unscored infrastructure failures.

The public report binds this policy as
`contract-exhausted-answer-is-wrong-v1` and records the number of affected
questions.

## Consequences

- A successful but unusable model response cannot disappear from the score.
- Exact repair composition can complete without paying for score-selection
  retries.
- The distinction between model quality and infrastructure reliability remains
  explicit and auditable.
- Historical smoke runs remain unscored and retain their infrastructure-failure
  accounting.
