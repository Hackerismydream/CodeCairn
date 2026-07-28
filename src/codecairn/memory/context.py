from __future__ import annotations

from dataclasses import dataclass

from codecairn.memory.models import RankedRecall, RecallSnippet

TOKENIZER_ID = "codecairn/utf8-two-byte-upper-bound-v1"
RENDERER_ID = "codecairn/typed-excerpt-context-v2"


@dataclass(frozen=True, slots=True)
class CompiledContext:
    markdown: str
    rendered_ids: tuple[str, ...]
    rendered_fact_ids: tuple[str, ...]
    omitted_ids: tuple[str, ...]
    omitted_snippet_count: int
    token_count: int


def count_tokens(value: str) -> int:
    return (len(value.encode()) + 1) // 2


def compile_context(query: str, ranked: tuple[RankedRecall, ...], *, token_limit: int) -> CompiledContext:
    if not 256 <= token_limit <= 32_768:
        raise ValueError("Recall token budget must be between 256 and 32768")
    header = f"# Recall Context\n\nTask: {query}\n"
    if count_tokens(header) > token_limit:
        header = _fit(f"# Recall Context\n\nTask: {query}", token_limit) + "\n"
    candidates: list[tuple[RankedRecall, RecallSnippet]] = []
    omitted_snippets = 0
    for memory in ranked:
        snippets = memory.snippets
        if not snippets:
            lines = tuple(line.strip() for line in memory.summary.splitlines() if line.strip())
            base = f"{memory.memory_id}:memory:"
            snippets = tuple(RecallSnippet(f"{base}{i:04d}", line, memory.final_score - i * 1e-9) for i, line in enumerate(lines[:12]))
            omitted_snippets += max(0, len(lines) - len(snippets))
        candidates.extend((memory, snippet) for snippet in snippets)
    candidates.sort(key=lambda item: (-item[1].final_score, item[0].rank, item[1].document_id))
    selected: dict[str, list[RecallSnippet]] = {}
    for memory, snippet in candidates:
        bucket = selected.setdefault(memory.memory_id, [])
        bucket.append(snippet)
        if count_tokens(_render(header, ranked, selected)) <= token_limit:
            continue
        bucket.pop()
        omitted_snippets += 1
        if not bucket:
            del selected[memory.memory_id]
    markdown = _render(header, ranked, selected)
    rendered = tuple(memory.memory_id for memory in ranked if selected.get(memory.memory_id))
    omitted = tuple(memory.memory_id for memory in ranked if not selected.get(memory.memory_id))
    if not rendered and count_tokens(markdown + "\nNo attributed memory matched within the context budget.\n") <= token_limit:
        markdown += "\nNo attributed memory matched within the context budget.\n"
    return CompiledContext(
        markdown,
        rendered,
        tuple(
            snippet.document_id.split(":fact:", 1)[1]
            for memory in ranked
            for snippet in selected.get(memory.memory_id, ())
            if ":fact:" in snippet.document_id
        ),
        omitted,
        omitted_snippets,
        count_tokens(markdown),
    )


def _render(header: str, ranked: tuple[RankedRecall, ...], selected: dict[str, list[RecallSnippet]]) -> str:
    blocks = []
    for memory in ranked:
        snippets = selected.get(memory.memory_id)
        if snippets:
            blocks.append(
                f"\n## {memory.title}\n\n"
                + "".join(f"- {snippet.text}\n" for snippet in snippets)
                + f"Memory: {memory.memory_id}\nType: {memory.memory_type}\nStatus: {memory.status}\nSource: {memory.source_uri}\n"
            )
    return header + "".join(blocks)


def _fit(value: str, token_limit: int) -> str:
    return value.encode()[: token_limit * 2].decode(errors="ignore").rstrip()
