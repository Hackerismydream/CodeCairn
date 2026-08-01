---
status: accepted
---

# Version 0.3 Hub Acceptance Requires Machine and Blind Human Evidence

## Context

ADR 0061 established the foreground, loopback-only presentation used by the
read-only Hub. A working browser-to-application path is necessary, but it does
not prove the version 0.3 user outcome: a first-time target learner can
understand what an Agent remembered, where that memory came from, why it was
recalled, and whether an older memory still participates in default recall.

A screenshot, fixture, manually written observation, or developer walkthrough
can make the Hub look complete without proving that Pico produced the memory,
that a fresh Pico process consumed it, or that a learner understood the
evidence. Conversely, a comprehension questionnaire alone cannot prove that
the page was backed by the exact candidate under test.

Version 0.3 therefore needs one acceptance contract that joins runtime
behavior, public CodeCairn evidence, all three Hub views, and independently
reviewed human answers without treating any one of those as a substitute for
the others.

## Decision

CodeCairn owns the version 0.3 acceptance campaign and its immutable artifact.
Pico is an invoked Runtime adapter: it performs the Agent turns but does not
own the protocol, result reducer, promotion threshold, or release claim.

The frozen protocol uses a small retry-policy project. Task A asks Pico to
change `DEFAULT_RETRIES` from 2 to 4, run the tests, and retain a stated reason
that must not be written into the project. An external verifier, not the
Agent's success message, requires the exact source change, an unchanged frozen
test, absence of the hidden decision marker, and a passing fixed unittest
command. Task B starts in a different Pico process and asks why the value
changed and how it was verified.
The runner pins the protocol's canonical digest; changing its prompts, rubric,
or five-participant and 4/5 thresholds creates a different, unsupported
protocol rather than a version 0.3 campaign.

The technical machine gate requires all of the following:

1. exact CodeCairn and Pico candidate identity;
2. installed Pico with Memory Backend `codecairn`;
3. a real Pico subprocess trace with a successful configured-LLM call for
   Task A, without claiming that the endpoint is a hosted provider;
4. a distinct, fresh Pico process for Task B;
5. a captured Pico Task Experience joined to Task B recall and the first LLM
   input that used it, with tools disabled for the recall turn;
6. capture and recall evidence derived through public `codecairn list` and
   `codecairn recall` results rather than direct SQLite or Markdown reads;
7. successful Hub Memories, Recall, and System reads against the same
   repository identity; and
8. a scenario-seeded predecessor/successor pair showing `superseded` and
   `active` lifecycle states plus the immutable Supersession relation, with the
   predecessor absent from default ranked and rendered Recall.

Provider failure, process failure, an incompatible public contract, or missing
evidence is an explicit failure or non-evaluable result. It is never converted
to a fixture-backed pass.

After the machine gate passes, five eligible first-time target learners use the
Chinese Hub and answer four questions: what was remembered, where it came
from, why it was recalled, and whether the old memory still participates in
default recall and what replaced it. Each response is bound to the campaign
manifest and Hub snapshot digests; source, bundle, and live Hub equality are
checked before the form opens and again before the response is written.
Eligibility requires a human target learner with no prior CodeCairn exposure,
no CodeCairn contribution, consent to local evidence, and zero moderator
content hints. Participant and reviewer identities are human attestations, not
cryptographically authenticated real-world identities.

A separate human reviewer scores each answer against machine-derived ground
truth using the frozen rubric. There is no LLM judge. Promotion requires all
five participants to be valid, at least four participants to pass all four
questions, and at least four passes for each question.

The campaign is append-only during collection. `seal` writes a summary and a
content inventory. `verify` recomputes the verdict offline and rejects changed,
missing, or extra files.
This offline verifier recomputes the verdict from normalized evidence and
checks selected raw-receipt bindings; it does not semantically rederive every
normalized field from raw Pico and Hub payloads. The source collector is
therefore trusted at collection time.

Delivery identity is part of the verdict:

- `source_checkout` may establish the technical machine gate and support a
  pilot, but it is never release-eligible;
- `release_artifact` requires SHA-256 identities for the CodeCairn, Pico, and
  Hub artifacts;
- release promotion additionally requires an installed Hub distribution and a
  raw installed-artifact collector. `source-pilot` derives source-checkout
  evidence from public commands and traces; the manual normalized observation
  ingress is diagnostic-only. The verifier remains fail-closed for formal
  release until the installed collector exists.

No current artifact proves that the configured-LLM Pico run,
five-participant study, blind human review, or formal release collection has
occurred. The presence of the protocol and runner is implementation evidence,
not a completed version 0.3 acceptance result.

ADR 0049 and ADR 0058 remain unchanged for their frozen version 0.1 and version
0.2 stages. Add a `v03-acceptance` source-budget stage with these maintained
roots:

- `src/codecairn`;
- `apps/hub-api/src/codecairn_hub_api`;
- `scripts/run_hub.py`; and
- `tools/v03-acceptance/src/codecairn_v03_acceptance`;
- maintained Hub Web application, library, worker, and configuration
  `.ts`, `.tsx`, and `.css` sources.

The Hub API, launcher, and maintained Hub Web sources count as product core.
The acceptance runner counts as evaluation. The stage ceilings are 16,200
physical source lines for core and 25,000 total. These are additive delivery
ceilings, not a relabeling of the version 0.1 release budget; tests, protocols,
scenarios, and documentation are outside the count.

## Consequences

- A functional Hub can remain a version 0.3 candidate while human evidence is
  incomplete; it cannot be described as formally accepted.
- CodeCairn owns one reproducible evidence boundary while Pico remains
  replaceable as the invoked Agent Runtime.
- Human comprehension cannot be inferred from machine success, and runtime
  continuity cannot be inferred from human answers.
- Scenario-seeded Supersession proves that the Hub explains lifecycle; it is
  not evidence that Pico or a model independently proposed that evolution.
- A manually prepared normalized observation can exercise the reducer but
  cannot promote a release artifact.
- The source-checkout Hub and its acceptance adapters remain local and
  foreground. This decision does not add a daemon, remote API, account model,
  or memory-governance operation.
- A future formal version 0.3 campaign must check in or publish its sealed,
  offline-verifiable bundle before any release or interview-material claim is
  made from it.
