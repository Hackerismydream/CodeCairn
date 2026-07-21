from __future__ import annotations

import hashlib
import json

from codecairn.memory.models import (
    CodingMemory,
    EvidenceFact,
    RecallDocument,
    RecallDocumentFingerprint,
    RecallDocumentKind,
)
from codecairn.memory.trace import stable_id

_MAX_EPISODE_PROJECTION_CHARS = 16_000


def project_recall_documents(
    memory: CodingMemory,
    *,
    markdown: str,
) -> tuple[RecallDocument, ...]:
    """Build the disposable Episode/AtomicFact recall projection."""
    if memory.content_sha256 is None:
        raise ValueError("Cannot project a memory without a Markdown digest")

    episode_document_id = stable_id(
        "recall-episode",
        memory.repo_key,
        memory.memory_id,
    )
    episode = _document(
        document_id=episode_document_id,
        memory=memory,
        document_kind="episode",
        parent_document_id="",
        source_episode_id=memory.episode_id,
        fact_id="",
        title=memory.title,
        summary=memory.summary,
        content=_episode_projection_content(memory, markdown=markdown),
        child_count=len(memory.facts),
    )
    children = tuple(
        _atomic_fact_document(
            memory,
            fact=fact,
            parent_document_id=episode_document_id,
        )
        for fact in memory.facts
    )
    return (episode, *children)


def _episode_projection_content(memory: CodingMemory, *, markdown: str) -> str:
    if not markdown.startswith("---\n"):
        body = markdown
    else:
        frontmatter, separator, body = markdown[4:].partition("\n---\n")
        if separator:
            episode_frontmatter = "\n".join(
                line for line in frontmatter.splitlines() if not line.startswith("facts: ")
            )
            body = f"---\n{episode_frontmatter}\n---\n{body}"
        else:
            body = markdown
    fact_text = "\n".join(f"- {fact.text}" for fact in memory.facts)
    content = f"{body}\n\nEpisode facts:\n{fact_text}"
    if len(content) <= _MAX_EPISODE_PROJECTION_CHARS:
        return content
    return content[: _MAX_EPISODE_PROJECTION_CHARS - 1].rstrip() + "…"


def fingerprint(document: RecallDocument) -> RecallDocumentFingerprint:
    return RecallDocumentFingerprint(
        repo_key=document.repo_key,
        memory_id=document.memory_id,
        document_id=document.document_id,
        document_kind=document.document_kind,
        parent_document_id=document.parent_document_id,
        fact_id=document.fact_id,
        document_sha256=document.document_sha256,
    )


def compute_document_sha256(
    *,
    document_id: str,
    repo_key: str,
    memory_id: str,
    document_kind: RecallDocumentKind,
    parent_document_id: str,
    source_episode_id: str,
    fact_id: str,
    content_sha256: str,
    memory_type: str,
    title: str,
    summary: str,
    content: str,
    child_count: int,
) -> str:
    payload = {
        "document_id": document_id,
        "repo_key": repo_key,
        "memory_id": memory_id,
        "document_kind": document_kind,
        "parent_document_id": parent_document_id,
        "source_episode_id": source_episode_id,
        "fact_id": fact_id,
        "content_sha256": content_sha256,
        "memory_type": memory_type,
        "title": title,
        "summary": summary,
        "content": content,
        "child_count": child_count,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _atomic_fact_document(
    memory: CodingMemory,
    *,
    fact: EvidenceFact,
    parent_document_id: str,
) -> RecallDocument:
    status = f"\nStatus: {fact.status}" if fact.status is not None else ""
    return _document(
        document_id=stable_id(
            "recall-atomic-fact",
            memory.repo_key,
            memory.memory_id,
            fact.fact_id,
        ),
        memory=memory,
        document_kind="atomic_fact",
        parent_document_id=parent_document_id,
        source_episode_id=fact.episode_id,
        fact_id=fact.fact_id,
        title=fact.kind.replace("_", " ").title(),
        summary=fact.text,
        content=f"{fact.kind}\n{fact.text}{status}",
        child_count=0,
    )


def _document(
    *,
    document_id: str,
    memory: CodingMemory,
    document_kind: RecallDocumentKind,
    parent_document_id: str,
    source_episode_id: str,
    fact_id: str,
    title: str,
    summary: str,
    content: str,
    child_count: int,
) -> RecallDocument:
    if memory.content_sha256 is None:
        raise ValueError("Cannot project a memory without a Markdown digest")
    document_sha256 = compute_document_sha256(
        document_id=document_id,
        repo_key=memory.repo_key,
        memory_id=memory.memory_id,
        document_kind=document_kind,
        parent_document_id=parent_document_id,
        source_episode_id=source_episode_id,
        fact_id=fact_id,
        content_sha256=memory.content_sha256,
        memory_type=memory.memory_type,
        title=title,
        summary=summary,
        content=content,
        child_count=child_count,
    )
    return RecallDocument(
        document_id=document_id,
        repo_key=memory.repo_key,
        memory_id=memory.memory_id,
        document_kind=document_kind,
        parent_document_id=parent_document_id,
        source_episode_id=source_episode_id,
        fact_id=fact_id,
        content_sha256=memory.content_sha256,
        document_sha256=document_sha256,
        memory_type=memory.memory_type,
        title=title,
        summary=summary,
        content=content,
        child_count=child_count,
    )
