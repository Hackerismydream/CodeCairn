from __future__ import annotations

import hashlib

from codecairn.memory.models import DocumentKind, MemoryStatus, RecallDocument
from codecairn.memory.schema import CodingMemory, WorkStatePayload, canonical_json


def project_memory(memory: CodingMemory, *, status: MemoryStatus) -> tuple[RecallDocument, ...]:
    parent = _document(
        memory,
        status,
        f"{memory.memory_id}:memory",
        "memory",
        memory.title,
        "\n".join((memory.title, memory.content, memory.category, " ".join(memory.tags), *(fact.value for fact in memory.facts))),
    )
    facts = tuple(
        _document(
            memory, status, f"{memory.memory_id}:fact:{fact.fact_id}", "fact", fact.fact_kind.replace("_", " ").title(), fact.value
        )
        for fact in memory.facts
    )
    lines = () if facts else tuple(line.strip() for line in memory.content.splitlines() if line.strip())[:128]
    snippets = (
        tuple(
            _document(memory, status, f"{memory.memory_id}:snippet:{index:04d}", "snippet", memory.title, line)
            for index, line in enumerate(lines)
        )
        if len(lines) > 1
        else ()
    )
    return (parent, *facts, *snippets)


def _document(
    memory: CodingMemory, status: MemoryStatus, document_id: str, kind: DocumentKind, title: str, content: str
) -> RecallDocument:
    digest = hashlib.sha256(
        canonical_json({"schema_version": 1, "document_id": document_id, "status": status, "content": content}).encode()
    ).hexdigest()
    head = (document_id, kind, memory.repo_key, memory.memory_id, memory.memory_type, status, title, content)
    workstream = memory.payload.workstream_key if isinstance(memory.payload, WorkStatePayload) else None
    return RecallDocument(*head, digest, memory.created_at_ms, workstream)
