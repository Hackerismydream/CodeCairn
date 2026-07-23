from __future__ import annotations

CONTEXT_TOKENIZER_ID = "codecairn/utf8-two-byte-upper-bound-v1"
CONTEXT_RENDERER_ID = "scored-facts-first-v5"
TOKEN_BUDGET_CONTEXT_RENDERERS = frozenset(
    {
        "facts-first-round-robin-v4",
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
