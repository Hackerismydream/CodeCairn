from __future__ import annotations

from collections.abc import Iterable

import pytest

import codecairn.memory.embedding as embedding_module
import codecairn.memory.reranking as reranking_module
from codecairn.bootstrap import create_retrieval_providers
from codecairn.memory.embedding import FastEmbedEmbeddingAdapter
from codecairn.memory.model_artifact import fastembed_version
from codecairn.memory.models import RerankDocument
from codecairn.memory.reranking import FastEmbedRerankingAdapter


class FakeEmbeddingModel:
    def query_embed(self, query: str) -> Iterable[tuple[float, ...]]:
        assert query == "query text"
        return ((1.0, 0.0, 0.0),)

    def passage_embed(self, texts: Iterable[str]) -> Iterable[tuple[float, ...]]:
        assert list(texts) == ["document one", "document two"]
        return ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class FakeCrossEncoder:
    def rerank(self, query: str, documents: Iterable[str]) -> Iterable[float]:
        assert query == "query text"
        assert list(documents) == ["document one", "document two"]
        return (-2.5, 4.25)


def test_fastembed_adapters_load_lazily_and_preserve_model_scores(monkeypatch) -> None:
    embedding_loads: list[tuple[str, str, str, str | None]] = []
    reranker_loads: list[tuple[str, str, str, str | None]] = []
    monkeypatch.setattr(
        embedding_module,
        "_load_fastembed_model",
        lambda model_id, source_id, revision, cache_dir: (
            embedding_loads.append((model_id, source_id, revision, cache_dir))
            or FakeEmbeddingModel()
        ),
    )
    monkeypatch.setattr(
        reranking_module,
        "_load_fastembed_reranker",
        lambda model_id, source_id, revision, cache_dir: (
            reranker_loads.append((model_id, source_id, revision, cache_dir)) or FakeCrossEncoder()
        ),
    )
    embedder = FastEmbedEmbeddingAdapter(
        model_id="test/embedding",
        source_id="test/embedding-source",
        revision="a" * 40,
        dimension=3,
        cache_dir="/models",
    )
    reranker = FastEmbedRerankingAdapter(
        model_id="test/reranker",
        source_id="test/reranker-source",
        revision="b" * 40,
        cache_dir="/models",
    )

    assert embedding_loads == []
    assert reranker_loads == []
    assert embedder.embed_query("query text") == (1.0, 0.0, 0.0)
    assert embedder.embed_documents(("document one", "document two")) == (
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    scores = reranker.rerank(
        "query text",
        (
            RerankDocument("memory-a", "document one", 0.02),
            RerankDocument("memory-b", "document two", 0.01),
        ),
    )

    assert embedding_loads == [("test/embedding", "test/embedding-source", "a" * 40, "/models")]
    assert reranker_loads == [("test/reranker", "test/reranker-source", "b" * 40, "/models")]
    assert [(item.memory_id, item.score) for item in scores] == [
        ("memory-a", -2.5),
        ("memory-b", 4.25),
    ]


def test_production_retrieval_profile_uses_learned_models_without_loading_them() -> None:
    providers = create_retrieval_providers(environment={})

    assert providers.public_config == {
        "method": "hybrid-rrf-cross-encoder",
        "embedding": {
            "adapter": "fastembed",
            "adapter_version": fastembed_version(),
            "adapter_license": "Apache-2.0",
            "model": "BAAI/bge-small-en-v1.5",
            "source": "qdrant/bge-small-en-v1.5-onnx-q",
            "revision": "52398278842ec682c6f32300af41344b1c0b0bb2",
            "dimension": 384,
            "license": "MIT",
        },
        "reranker": {
            "adapter": "fastembed-cross-encoder",
            "adapter_version": fastembed_version(),
            "adapter_license": "Apache-2.0",
            "model": "Xenova/ms-marco-MiniLM-L-6-v2",
            "source": "Xenova/ms-marco-MiniLM-L-6-v2",
            "revision": "a09144355adeed5f58c8ed011d209bf8ee5a1fec",
            "license": "Apache-2.0",
        },
        "planner": {
            "mode": "hierarchy",
            "router": "deterministic-cues-v1",
            "hard_route_cutoff": False,
            "neighbor_window": 1,
            "matched_facts_per_memory": 3,
            "sibling_facts_per_memory": 2,
        },
    }


def test_recall_mode_selects_an_auditable_ablation_configuration() -> None:
    providers = create_retrieval_providers(
        environment={
            "CODECAIRN_RETRIEVAL_PROFILE": "hashing-test",
            "CODECAIRN_RECALL_MODE": "episode-only",
        }
    )

    assert providers.planner.atomic_fact_enabled is False
    assert providers.public_config["planner"] == {
        "mode": "episode-only",
        "router": "deterministic-cues-v1",
        "hard_route_cutoff": False,
        "neighbor_window": 0,
        "matched_facts_per_memory": 3,
        "sibling_facts_per_memory": 2,
    }


def test_recall_mode_rejects_unknown_ablation_names() -> None:
    with pytest.raises(ValueError, match="Unknown recall mode"):
        create_retrieval_providers(environment={"CODECAIRN_RECALL_MODE": "experimental"})


def test_custom_embedding_model_requires_an_explicit_dimension() -> None:
    with pytest.raises(ValueError, match="CODECAIRN_EMBEDDING_DIMENSION"):
        create_retrieval_providers(environment={"CODECAIRN_EMBEDDING_MODEL": "custom/model"})


def test_embedding_adapter_rejects_a_model_dimension_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        embedding_module,
        "_load_fastembed_model",
        lambda model_id, source_id, revision, cache_dir: FakeEmbeddingModel(),
    )
    embedder = FastEmbedEmbeddingAdapter(
        model_id="test/embedding",
        source_id="test/embedding-source",
        revision="a" * 40,
        dimension=4,
    )

    with pytest.raises(ValueError, match="returned 3 dimensions; expected 4"):
        embedder.embed_query("query text")


def test_retrieval_profile_rejects_movable_model_revisions() -> None:
    with pytest.raises(ValueError, match="40-character commit SHA"):
        create_retrieval_providers(environment={"CODECAIRN_EMBEDDING_REVISION": "main"})


def test_artifact_override_requires_an_explicit_declared_license() -> None:
    with pytest.raises(ValueError, match="CODECAIRN_EMBEDDING_LICENSE"):
        create_retrieval_providers(
            environment={
                "CODECAIRN_EMBEDDING_SOURCE": "other/compatible-onnx",
                "CODECAIRN_EMBEDDING_REVISION": "c" * 40,
            }
        )


def test_fastembed_loaders_use_resolved_snapshot_paths_and_eager_inner_loading(
    monkeypatch,
) -> None:
    import fastembed
    import fastembed.rerank.cross_encoder as cross_encoder_module

    downloads: list[tuple[str, str, str | None]] = []
    constructors: list[tuple[str, dict[str, object]]] = []

    def download(*, source_id: str, revision: str, cache_dir: str | None) -> str:
        downloads.append((source_id, revision, cache_dir))
        return f"/snapshots/{revision}"

    def embedding_constructor(**kwargs):
        constructors.append(("embedding", kwargs))
        return FakeEmbeddingModel()

    def reranker_constructor(**kwargs):
        constructors.append(("reranker", kwargs))
        return FakeCrossEncoder()

    monkeypatch.setattr(embedding_module, "download_hf_snapshot", download)
    monkeypatch.setattr(reranking_module, "download_hf_snapshot", download)
    monkeypatch.setattr(fastembed, "TextEmbedding", embedding_constructor)
    monkeypatch.setattr(cross_encoder_module, "TextCrossEncoder", reranker_constructor)

    embedding_module._load_fastembed_model(
        "test/embedding",
        "test/embedding-source",
        "a" * 40,
        "/models",
    )
    reranking_module._load_fastembed_reranker(
        "test/reranker",
        "test/reranker-source",
        "b" * 40,
        "/models",
    )

    assert downloads == [
        ("test/embedding-source", "a" * 40, "/models"),
        ("test/reranker-source", "b" * 40, "/models"),
    ]
    assert constructors == [
        (
            "embedding",
            {
                "model_name": "test/embedding",
                "cache_dir": "/models",
                "specific_model_path": "/snapshots/" + "a" * 40,
                "lazy_load": False,
            },
        ),
        (
            "reranker",
            {
                "model_name": "test/reranker",
                "cache_dir": "/models",
                "specific_model_path": "/snapshots/" + "b" * 40,
                "lazy_load": False,
            },
        ),
    ]
