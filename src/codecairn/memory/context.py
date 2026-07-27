from __future__ import annotations

from dataclasses import dataclass

from codecairn.memory.models import RankedRecall

TOKENIZER_ID = "codecairn/utf8-two-byte-upper-bound-v1"
RENDERER_ID = "codecairn/typed-context-v1"


@dataclass(frozen=True, slots=True)
class CompiledContext:
    markdown: str
    rendered_ids: tuple[str, ...]
    omitted_ids: tuple[str, ...]
    token_count: int


def count_tokens(value: str) -> int:
    return (len(value.encode()) + 1) // 2


def compile_context(
    query: str,
    ranked: tuple[RankedRecall, ...],
    *,
    token_limit: int,
) -> CompiledContext:
    if not 256 <= token_limit <= 32_768:
        raise ValueError("Recall token budget must be between 256 and 32768")
    header = f"# Recall Context\n\nTask: {query}\n"
    if count_tokens(header) > token_limit:
        header = _fit(f"# Recall Context\n\nTask: {query}", token_limit) + "\n"
    markdown = header
    rendered: list[str] = []
    omitted: list[str] = []
    for item in ranked:
        section = (
            f"\n## {item.title}\n\n"
            f"{item.summary}\n\n"
            f"Memory: {item.memory_id}\n"
            f"Type: {item.memory_type}\n"
            f"Status: {item.status}\n"
            f"Source: {item.source_uri}\n"
        )
        if count_tokens(markdown + section) > token_limit:
            omitted.append(item.memory_id)
            continue
        markdown += section
        rendered.append(item.memory_id)
    if not rendered:
        empty = "\nNo attributed memory matched within the context budget.\n"
        if count_tokens(markdown + empty) <= token_limit:
            markdown += empty
    return CompiledContext(
        markdown=markdown,
        rendered_ids=tuple(rendered),
        omitted_ids=tuple(omitted),
        token_count=count_tokens(markdown),
    )


def _fit(value: str, token_limit: int) -> str:
    maximum_bytes = token_limit * 2
    return value.encode()[:maximum_bytes].decode(errors="ignore").rstrip()
