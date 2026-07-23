from __future__ import annotations

CONTEXT_TOKENIZER_ID = "codecairn/utf8-two-byte-upper-bound-v1"
CONTEXT_DIRECT_MATCH_PRIOR = 2.0
LEGACY_CONTEXT_EVIDENCE_SLOT_POLICY_ID = "typed-protected-child-support-v1"
CONTEXT_EVIDENCE_SLOT_POLICY_ID = "typed-protected-child-support-v2"
CONTEXT_EVIDENCE_SLOT_POLICY_IDS = frozenset(
    {
        LEGACY_CONTEXT_EVIDENCE_SLOT_POLICY_ID,
        CONTEXT_EVIDENCE_SLOT_POLICY_ID,
    }
)
LEGACY_EXACT_SOURCE_CONTEXT_RENDERER_ID = "exact-source-prioritized-facts-first-v7"
CONTEXT_RENDERER_ID = "exact-source-coverage-aware-facts-first-v8"
CONTEXT_EVIDENCE_SLOT_KINDS = frozenset(
    {
        "prior_state",
        "quantity_transition",
        "semantic_child_support",
        "vocative_alias",
    }
)
CONTEXT_SLOT_ADMISSION_OUTCOMES = frozenset(
    {
        "admitted",
        "budget",
        "duplicate",
        "parent_limit",
    }
)
TOKEN_BUDGET_CONTEXT_RENDERERS = frozenset(
    {
        "facts-first-round-robin-v4",
        "scored-facts-first-v5",
        "exact-source-facts-first-v6",
        LEGACY_EXACT_SOURCE_CONTEXT_RENDERER_ID,
        CONTEXT_RENDERER_ID,
    }
)


def count_context_tokens(text: str) -> int:
    """Count conservative, provider-independent context units.

    Two UTF-8 bytes per unit deliberately over-count ordinary English and CJK
    memory text while keeping the runtime independent from a remote tokenizer.
    The identity is pinned in every recall trace so benchmark comparisons do not
    present this deterministic budget as a provider-reported token count.
    """

    byte_count = len(text.encode("utf-8"))
    return (byte_count + 1) // 2
