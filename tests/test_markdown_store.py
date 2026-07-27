from __future__ import annotations

import os

import pytest

from codecairn.memory.schema import CodingMemory, IdentityConflict, LegacyRootUnsupported, RepositoryKnowledgePayload, SchemaInvalid
from codecairn.storage.markdown import MarkdownMemoryStore


def _memory(*, content: str = "Run make check before committing.") -> CodingMemory:
    return CodingMemory.create(
        repo_key="acme/widgets",
        memory_type="repository_knowledge",
        title="Repository checks",
        content=content,
        category="command",
        tags=("checks",),
        created_at_ms=1,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=RepositoryKnowledgePayload(subject_key="repository-checks", claim=content),
    )


def test_markdown_round_trip_is_idempotent_and_storage_metadata_is_external(tmp_path) -> None:
    store = MarkdownMemoryStore(tmp_path)
    memory = _memory()

    first = store.write(memory)
    second = store.write(memory)
    restored = store.read(first.path)

    assert first == second == restored
    assert restored.memory == memory
    assert "/memory/" in str(restored.path)
    assert len(restored.content_sha256) == 64
    assert not hasattr(memory, "markdown_path")
    assert not hasattr(memory, "content_sha256")


def test_markdown_body_is_exact_memory_content(tmp_path) -> None:
    memory = _memory(content="Line one.\n\nLine two.")
    artifact = MarkdownMemoryStore(tmp_path).write(memory)
    source = artifact.path.read_text()

    assert source.endswith("---\nLine one.\n\nLine two.\n")
    assert "\ncontent: " not in source


def test_conflicting_identity_does_not_overwrite_truth(tmp_path) -> None:
    store = MarkdownMemoryStore(tmp_path)
    memory = _memory()
    artifact = store.write(memory)
    source = artifact.path.read_text()
    artifact.path.write_text(source.replace("Run make check", "Skip make check"))

    with pytest.raises(IdentityConflict):
        store.write(memory)


def test_unknown_or_reordered_frontmatter_is_rejected(tmp_path) -> None:
    store = MarkdownMemoryStore(tmp_path)
    artifact = store.write(_memory())
    source = artifact.path.read_text()
    artifact.path.write_text(source.replace("record_kind:", "unknown:\nrecord_kind:", 1))

    with pytest.raises(SchemaInvalid):
        store.read(artifact.path)


def test_legacy_root_and_non_regular_truth_fail_before_write(tmp_path) -> None:
    (tmp_path / "repos").mkdir()
    with pytest.raises(LegacyRootUnsupported):
        MarkdownMemoryStore(tmp_path).write(_memory())

    clean = tmp_path / "clean"
    store = MarkdownMemoryStore(clean)
    target = store.path_for(_memory())
    target.parent.mkdir(parents=True)
    os.symlink(tmp_path / "missing", target)
    with pytest.raises(SchemaInvalid):
        store.write(_memory())
