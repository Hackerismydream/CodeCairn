# CodeCairn Memory Hub visual system

## Product surface

The read-only Hub has three primary views:

- Memories lists durable records and inspects content, evidence, and evolution.
- Recall runs a real task and explains its admitted or abstained
  `RecallResult`.
- System renders a point-in-time Doctor snapshot.

The Hub defaults to Memories. It has no dashboard overview, product roadmap,
account surface, remote state, or governance actions.

## Visual direction

Use a quiet native utility style. The shell is neutral and information appears
as grouped rows with one-pixel separators. Evidence and recall decisions carry
the visual hierarchy.

Color roles:

- `canvas`: `oklch(0.965 0.004 250)`
- `surface`: `oklch(0.995 0.002 250)`
- `sidebar`: `oklch(0.945 0.006 250)`
- `inspector`: `oklch(0.975 0.004 250)`
- `label`: `oklch(0.235 0.008 250)`
- `secondary`: `oklch(0.52 0.01 250)`
- `separator`: `oklch(0.86 0.006 250)`
- `accent`: `oklch(0.62 0.18 252)`, selection only

Use the Apple system font stack for product text and SF Mono or Menlo for
identifiers. Avoid decorative icons, gradients, glass blur, entrance animation,
marketing cards, colored type tiles, and English eyebrow labels.

## Interaction rules

Every button must perform one of these actions:

1. navigate between Memories, Recall, and System;
2. filter the memory list;
3. select one memory;
4. switch the memory inspector tab;
5. page a real memory list;
6. run or retry a real read request.

## Evidence and contract rules

- Keep `repo_key`, memory identifiers, resource URIs, status, and provenance
  visible but secondary.
- Present recall lifecycle and event-evidence presence as separate facts.
  `active` means that a memory participates in default recall; it does not
  mean that the memory's content has been verified.
- Lead source inspection with the Agent, session, observed command outcome,
  and exit code. Keep fact identifiers and hashes in technical disclosure.
- Name vector similarity and ranked-result score separately; never imply that
  pending memory refinement means the current retrieval index is unfinished.
- Present Doctor only as a snapshot, never as daemon presence.
- Preserve `admission_trace`, `relevance` omissions, exact excerpt context, and
  renderer `codecairn/typed-excerpt-context-v2`.
- An abstention must show an empty ranked result and
  `No relevant memory was admitted.`
- Do not invent recall history, task effect, remote connectivity, or live Pico
  connection state.
- Empty or disconnected state must remain visible; never fall back to fixture
  data.

## Responsive behavior

Desktop uses a navigation rail and a two-column memory workbench. The inspector
moves below the list on narrower screens. At 620px navigation becomes a bottom
bar. Memory and recall rows reflow into stacked layouts; no table requires
horizontal page scrolling.
