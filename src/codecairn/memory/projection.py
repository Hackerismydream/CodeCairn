from __future__ import annotations

import hashlib
from typing import Literal

from codecairn.memory.models import MemoryStatus, RecallDocument
from codecairn.memory.schema import CodingMemory, WorkStatePayload, canonical_json


def project_memory(memory: CodingMemory, *, status: MemoryStatus) -> tuple[RecallDocument, ...]:
    parent = _document(
        memory,
        status=status,
        document_id=f"{memory.memory_id}:memory",
        document_kind="memory",
        title=memory.title,
        content="\n".join(
            (memory.title, memory.content, memory.category, " ".join(memory.tags), *(fact.value for fact in memory.facts))
        ),
    )
    return (
        parent,
        *(
            _document(
                memory,
                status=status,
                document_id=f"{memory.memory_id}:fact:{fact.fact_id}",
                document_kind="fact",
                title=fact.fact_kind.replace("_", " ").title(),
                content=fact.value,
            )
            for fact in memory.facts
        ),
    )


def _document(
    memory: CodingMemory, *, status: MemoryStatus, document_id: str, document_kind: Literal["memory", "fact"], title: str, content: str
) -> RecallDocument:
    digest = hashlib.sha256(
        canonical_json({"schema_version": 1, "document_id": document_id, "status": status, "content": content}).encode()
    ).hexdigest()
    return RecallDocument(
        document_id=document_id,
        document_kind=document_kind,
        repo_key=memory.repo_key,
        memory_id=memory.memory_id,
        memory_type=memory.memory_type,
        status=status,
        title=title,
        content=content,
        content_sha256=digest,
        created_at_ms=memory.created_at_ms,
        workstream_key=(memory.payload.workstream_key if isinstance(memory.payload, WorkStatePayload) else None),
    )
