# Version 0.3 Hub Acceptance

Status: acceptance infrastructure implemented; no formal version 0.3 campaign
has completed.

This runbook defines what would turn the checked-in read-only Hub from a
technical implementation into an accepted version 0.3 product outcome. It does
not report a configured-LLM run, a participant result, or a release verdict.

## Ownership and evidence boundary

CodeCairn owns the protocol, campaign manifest, machine reducer, participant
roster, blind-review rubric, sealing, and offline verification. Pico is the
invoked Agent Runtime used to produce and consume memory. The Hub is the
human-facing read adapter over the same CodeCairn namespace.

```text
frozen CodeCairn, Pico, and Hub identity
  -> installed Pico Task A
  -> public CodeCairn capture evidence
  -> fresh installed Pico Task B
  -> public CodeCairn recall evidence
  -> Hub Memories + Recall + System snapshot
  -> five bound participant responses
  -> separate human blind reviews
  -> seal
  -> offline verification
```

The evidence classes are deliberately separate:

| Evidence | What it can prove | What it cannot prove |
|---|---|---|
| Source-checkout machine pilot | The technical scenario, public contracts, fresh-process continuity, and three Hub reads can close | Installed Hub distribution, learner comprehension, or release eligibility |
| Participant response | What one eligible learner understood from one bound candidate snapshot | That the runtime really produced the snapshot |
| Blind human review | Whether an answer satisfies the frozen rubric | Runtime correctness on its own |
| Sealed release-artifact campaign | The complete frozen result, once the installed collector and all gates exist | Product behavior outside the frozen protocol |

A screenshot, fixture, handwritten passing JSON, or a developer-led demo is not
formal acceptance evidence. Infrastructure failure is not a product failure,
but it is also not a pass.

## Frozen scenario

The protocol is
[`hub-comprehension-v1.json`](../../tools/v03-acceptance/protocols/hub-comprehension-v1.json).
The runner pins its canonical digest, so a lookalike file cannot weaken the
five-participant and 4/5 thresholds while retaining the version 0.3 identity.
Its task project begins with:

```python
DEFAULT_RETRIES = 2
```

Task A asks Pico to change the value to 4 and run tests. The operational reason
is present in the task but must not be copied into code or documentation. The
external scenario verifier accepts only an exact integer assignment to 4,
requires the frozen test to remain unchanged, rejects extra files and symbolic
links, checks that the hidden decision marker did not enter the workspace, and
runs a fixed isolated unittest command. The Agent's own statement that tests
passed is not authoritative.

Task B asks why the default changed and how it was verified. It runs in a fresh
Pico process. The recall configuration disables tools, so a successful answer
must be joined to the memory present in the first LLM input rather than a
second filesystem inspection.

The lifecycle example is seeded by the scenario after the task memory exists.
It creates an inspectable predecessor/successor relation so the Hub can show
that:

- the predecessor is `superseded`, not deleted or expired;
- the successor is `active`;
- default recall excludes the predecessor; and
- the immutable Supersession record explains what replaced it.

This seed makes lifecycle comprehension testable. It does not claim that Pico
or a model autonomously inferred the Supersession.

## Technical machine gate

The reducer requires all ten checks:

| Check | Required observation |
|---|---|
| Exact candidate identity | Observed CodeCairn and Pico identities match the frozen campaign |
| Installed Pico | The invoked executable and public receipt identify an installed Pico |
| CodeCairn backend selected | Pico selected backend `codecairn` |
| Real Pico trace | Task A has one successful Pico subprocess Turn with a configured LLM call |
| Fresh-process continuity | Task A and Task B use distinct process and session identities |
| Evidence chain | A captured memory is present in public recall and the first Task B LLM input |
| Hub Memories | The same memory and its evidence can be inspected |
| Hub Recall | Admission, ranked/rendered memory identity, and explanation are readable |
| Hub System | The same repository and point-in-time health snapshot are readable |
| Supersession visible | The predecessor, active successor, and relation appear, while default ranked/rendered Recall excludes the predecessor |

Capture evidence comes from a before/after `codecairn list` comparison. The
collector accepts only a newly visible, capture-derived Pico Task Experience
whose source session matches Task A. Recall evidence comes from public
`codecairn recall` output and requires fresh, cursor-complete admission plus a
memory present in both ranking and rendered context. The collector does not
read SQLite or Markdown to manufacture these facts.

The Hub adapter launches the source-checkout Hub in the foreground and reads
only its same-origin Memories, Recall, and System routes. The launcher can
write an exclusive mode-`0600` ready receipt after both child services respond.
The receipt contains the loopback origin, ports, launcher PID, and child
process-group IDs; it contains no session token. Closing the launcher closes
the Hub.

`source-pilot` is the single operator command for this source-checkout machine
flow. It creates the campaign, stages an isolated Git scenario, runs both Pico
processes, verifies the task externally, seeds the lifecycle example, starts
the Hub, freezes its read snapshot, and derives the machine verdict. It
requires explicit `--live-authorized` because it may call the configured model
provider. A failed step writes a classified failure artifact and exits
non-zero; it never substitutes fixture evidence. `record-machine` remains a
manual diagnostic ingress and cannot satisfy the automated machine gate.

The source pilot records the frozen base-config digest and proves a successful
Pico LLM span, but it does not authenticate a remote provider, model, or base
URL. A local compatible endpoint is therefore configured execution, not a live
hosted-provider result.

## Human comprehension gate

The machine gate must pass before participant evidence is considered. The
frozen roster is `P001` through `P005`. Every participant must be:

- a human first-time target learner;
- not a CodeCairn contributor;
- willing to retain the local response as campaign evidence; and
- given zero moderator content hints.

Each participant uses the Chinese Hub and answers, in their own words:

1. what the Agent remembered about the retry change and verification;
2. which Pico session and Evidence Reference support that memory;
3. why Recall Admission accepted the memory for the current question; and
4. whether the predecessor participates in default recall and which active
   memory replaced it.

The participant form records the campaign-manifest digest and exact
`machine/hub-snapshot.json` digest. A response shown against another candidate
or modified snapshot is ineligible.

A separate human reviewer sees the frozen criteria and machine-derived ground
truth, then records `pass` or `fail` for each answer with an allowed reason
code. The participant cannot review their own response through the
participant form, and an LLM is never used as the judge.
Participant and reviewer identities are explicit human attestations, not
cryptographically authenticated real-world identities.

Formal promotion requires:

- five valid participants;
- at least four participants passing all four questions;
- at least four passes for every individual question; and
- all machine, identity, delivery, sealing, and inventory requirements.

## Campaign commands

Install the complete development workspace first:

```bash
uv sync --locked --all-packages --all-groups
```

Inspect the maintained operator surface:

```bash
uv run --package codecairn-v03-acceptance \
  codecairn-v03-acceptance --help
```

Run the machine pilot after preparing clean CodeCairn and Pico checkouts whose
installed console environments resolve back to those exact commits:

```bash
uv run --package codecairn-v03-acceptance \
  codecairn-v03-acceptance source-pilot \
  --protocol tools/v03-acceptance/protocols/hub-comprehension-v1.json \
  --output-root acceptance_results \
  --work-root <private-work-root> \
  --run-id <immutable-run-id> \
  --codecairn-commit <40-character-commit> \
  --pico-commit <40-character-commit> \
  --codecairn-checkout <clean-codecairn-checkout> \
  --pico-checkout <clean-pico-checkout> \
  --codecairn-executable <installed-codecairn-console> \
  --pico-executable <installed-pico-console> \
  --scenario-python <external-python-executable> \
  --base-pico-config <private-pico-config.json> \
  --fixture-dir "$PWD/tools/v03-acceptance/scenarios/retry-policy" \
  --repo-key <isolated-repository-key> \
  --live-authorized
```

The command validates clean checkout identity, installed console entry points,
and imported module locations before it calls Pico. The Pico environment must
contain the CodeCairn `pico.plugins` entry point from the frozen CodeCairn
checkout. Both task prompts and the semantic recall marker come from the
campaign's frozen protocol copy. The Hub production bundle is built as part of
the run. The output and work roots must be separate and unused for the run ID.

The lower-level `start` command exists for reducer diagnostics and future
installed-artifact collectors. A `release_artifact` campaign additionally
requires all three artifact SHA-256 values, but those values do not bypass the
currently missing installed Hub collector.

After machine evidence and the bound Hub snapshot exist, collect one response
and its separate review:

```bash
uv run --package codecairn-v03-acceptance \
  codecairn-v03-acceptance participant-source acceptance_results/<immutable-run-id> \
  --participant-id P001 \
  --codecairn-checkout <clean-codecairn-checkout> \
  --repository <private-work-root>/<immutable-run-id>/pico-learn/workspace

uv run --package codecairn-v03-acceptance \
  codecairn-v03-acceptance reviewer acceptance_results/<immutable-run-id> \
  --participant-id P001 \
  --reviewer-id <human-reviewer-id>
```

`participant-source` verifies the clean frozen CodeCairn commit, launches and
later reaps the source Hub, and rejects any live Memories, Recall, System, or
lifecycle projection that differs from the machine snapshot before it opens
the Chinese questionnaire. It repeats the clean-source, production-bundle, and
live-snapshot checks when the participant submits; the response is written only
after that guard passes. No arbitrary-Hub participant ingress can create formal
questionnaire evidence.

Repeat those two separately for the full frozen roster. Then seal and verify:

```bash
uv run --package codecairn-v03-acceptance \
  codecairn-v03-acceptance seal acceptance_results/<immutable-run-id>

uv run --package codecairn-v03-acceptance \
  codecairn-v03-acceptance verify acceptance_results/<immutable-run-id>
```

`seal` refuses incomplete evidence, writes `summary.json`, and inventories the
bundle. `verify` needs no provider credentials and rejects inventory or summary
drift. It recomputes the verdict from the normalized observation and human
records while checking selected raw receipts and inventory bindings. It does
not semantically reparse every Pico trace or Hub payload, so the source
collector remains trusted at collection time. The missing formal installed
collector must define the stronger release evidence boundary.

## Verdict meanings

| Outcome | Meaning |
|---|---|
| `awaiting_evidence` | A required machine, participant, or review artifact is absent |
| `not_evaluable` | Infrastructure failed or the human sample is not formally eligible |
| `fail` | A completed machine or comprehension threshold failed |
| `pass` | The measured thresholds passed; consult `release_eligible` and violations before making a release claim |

A source-checkout pass retains violation `delivery_mode_source_checkout`.
Formal release collection currently retains
`formal_release_collector_unavailable`. An unsealed campaign retains
`campaign_unsealed`. Therefore `outcome: pass` alone does not mean version 0.3
is release-eligible.

## Current remaining gates

The repository contains the protocol, scenario verifier, public CodeCairn
collector, Pico and Hub adapters, Chinese questionnaire surfaces, strict
reducer, seal, and offline verifier. The following have not been completed:

1. package and identity-test an installed Hub distribution;
2. implement the raw installed-artifact collector used for formal release;
3. execute the frozen scenario through real Pico processes and the declared
   configured LLM;
4. run five eligible first-time target learners without content hints;
5. complete separate human blind reviews; and
6. seal, publish, and offline-verify the resulting release-artifact bundle.

Until all six are evidenced, the accurate product statement is: the version
0.3 technical and comprehension acceptance infrastructure exists, while
formal version 0.3 acceptance remains pending.
