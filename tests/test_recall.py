from __future__ import annotations

from pathlib import Path

from codecairn.bootstrap import create_runtime
from codecairn.memory.schema import CodingMemory, RepositoryKnowledgePayload


def _knowledge(*, subject: str, content: str, created_at_ms: int) -> CodingMemory:
    return CodingMemory.create(
        repo_key="acme/widgets",
        memory_type="repository_knowledge",
        title=subject.replace("-", " ").title(),
        content=content,
        category="command",
        tags=(),
        created_at_ms=created_at_ms,
        episode_id=None,
        evidence=(),
        facts=(),
        origin="agent_asserted",
        restored_from=None,
        restore_predecessor_id=None,
        source_order_key=None,
        payload=RepositoryKnowledgePayload(subject_key=subject, claim=content),
    )


def test_recall_returns_ranked_context_with_source_uri(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime")
    runtime.store_memory(
        _knowledge(
            subject="repository-checks",
            content="Run make check before committing changes.",
            created_at_ms=1,
        )
    )
    runtime.store_memory(
        _knowledge(
            subject="database-migrations",
            content="SQLite migrations run during initialization.",
            created_at_ms=2,
        )
    )

    result = runtime.recall("How do I run repository checks?", repo_key="acme/widgets")

    assert result.sidecar.ranked[0].title == "Repository Checks"
    assert result.sidecar.ranked[0].candidate_sources == ("lexical",)
    assert result.sidecar.ranked[0].source_uri.startswith("codecairn://memory/mem_")
    assert "Run make check" in result.markdown


def test_recall_is_namespace_scoped_and_validates_limits(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime")
    runtime.store_memory(_knowledge(subject="checks", content="Run make check.", created_at_ms=1))

    assert runtime.recall("checks", repo_key="acme/other").sidecar.ranked == ()
