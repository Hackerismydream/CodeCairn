from __future__ import annotations

import json
import os
from collections.abc import Iterable

import httpx
import pytest

import codecairn.memory.embedding as embedding_module
import codecairn.memory.reranking as reranking_module
from codecairn.bootstrap import create_retrieval_providers
from codecairn.memory.embedding import DashScopeEmbeddingAdapter, FastEmbedEmbeddingAdapter
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
    def rerank(self, query: str, documents: Iterable[str], *, batch_size: int) -> Iterable[float]:
        assert query == "query text"
        assert list(documents) == ["document one", "document two"]
        assert batch_size == 2
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
        batch_size=2,
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


def test_production_retrieval_profile_uses_dashscope_without_calling_it() -> None:
    providers = create_retrieval_providers(environment={"DASHSCOPE_API_KEY": "secret-key"})

    assert providers.public_config == {
        "method": "hybrid-rrf-cross-encoder",
        "inference_threads": 1,
        "tokenizer_parallelism": False,
        "tokenizer_threads": 1,
        "embedding": {
            "adapter": "dashscope-openai-compatible",
            "adapter_version": "1",
            "model": "text-embedding-v4",
            "source": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "revision": "provider-managed",
            "dimension": 1024,
            "license": "Alibaba Cloud Model Studio service",
        },
        "reranker": {
            "adapter": "fastembed-cross-encoder",
            "adapter_version": fastembed_version(),
            "adapter_license": "Apache-2.0",
            "model": "Xenova/ms-marco-MiniLM-L-6-v2",
            "source": "Xenova/ms-marco-MiniLM-L-6-v2",
            "revision": "a09144355adeed5f58c8ed011d209bf8ee5a1fec",
            "license": "Apache-2.0",
            "batch_size": 8,
        },
        "planner": {
            "mode": "hierarchy",
            "router": "deterministic-cues-v1",
            "hard_route_cutoff": False,
            "primary_candidate_multiplier": 2,
            "secondary_candidate_multiplier": 1,
            "minimum_primary_candidates": 40,
            "minimum_secondary_candidates": 20,
            "neighbor_window": 1,
            "neighbor_snippet_budget": 20,
            "enrichment_order": "matched-adjacency-rerank-top-k-neighbors-v2",
            "matched_facts_per_memory": 3,
            "sibling_facts_per_memory": 2,
        },
    }


def test_dashscope_profile_defers_the_api_key_guard_until_embedding_is_used() -> None:
    providers = create_retrieval_providers(environment={})

    with pytest.raises(ValueError, match="CODECAIRN_EMBEDDING_API_KEY or DASHSCOPE_API_KEY"):
        providers.embedder.embed_query("query text")


def test_text_v4_profile_rejects_an_unsupported_dimension() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        create_retrieval_providers(
            environment={
                "DASHSCOPE_API_KEY": "secret-key",
                "CODECAIRN_EMBEDDING_DIMENSION": "384",
            }
        )


def test_text_v4_profile_rejects_batches_larger_than_the_provider_limit() -> None:
    with pytest.raises(ValueError, match="must not exceed 10"):
        create_retrieval_providers(
            environment={
                "DASHSCOPE_API_KEY": "secret-key",
                "CODECAIRN_EMBEDDING_BATCH_SIZE": "11",
            }
        )


def test_retrieval_profile_rejects_non_positive_reranker_batch_size() -> None:
    with pytest.raises(ValueError, match="RERANKER_BATCH_SIZE must be positive"):
        create_retrieval_providers(
            environment={
                "DASHSCOPE_API_KEY": "secret-key",
                "CODECAIRN_RERANKER_BATCH_SIZE": "0",
            }
        )


def test_dashscope_adapter_batches_openai_compatible_requests_and_restores_order() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        inputs = json.loads(request.content)["input"]
        data = [
            {"index": index, "embedding": [float(index), 1.0, 2.0]}
            for index in reversed(range(len(inputs)))
        ]
        return httpx.Response(200, json={"data": data}, request=request)

    embedder = DashScopeEmbeddingAdapter(
        api_key="secret-key",
        model_id="qwen3.7-text-embedding",
        base_url="https://dashscope.example/compatible-mode/v1/",
        revision="provider-managed",
        dimension=3,
        batch_size=2,
        retry_backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    assert embedder.embed_documents(("one", "two", "three")) == (
        (0.0, 1.0, 2.0),
        (1.0, 1.0, 2.0),
        (0.0, 1.0, 2.0),
    )
    assert [request.url.path for request in requests] == [
        "/compatible-mode/v1/embeddings",
        "/compatible-mode/v1/embeddings",
    ]
    assert [request.headers["authorization"] for request in requests] == [
        "Bearer secret-key",
        "Bearer secret-key",
    ]
    assert [request.read() for request in requests] == [
        b'{"model":"qwen3.7-text-embedding","input":["one","two"],"dimensions":3,"encoding_format":"float"}',
        b'{"model":"qwen3.7-text-embedding","input":["three"],"dimensions":3,"encoding_format":"float"}',
    ]


def test_dashscope_adapter_rejects_wrong_vector_dimensions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]},
            request=request,
        )

    embedder = DashScopeEmbeddingAdapter(
        api_key="secret-key",
        dimension=3,
        retry_backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValueError, match="returned 2 dimensions; expected 3"):
        embedder.embed_query("query text")


@pytest.mark.parametrize("invalid_value", (True, "1.0"))
def test_dashscope_adapter_rejects_non_numeric_vector_items(invalid_value: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [invalid_value, 2.0, 3.0]}]},
            request=request,
        )

    embedder = DashScopeEmbeddingAdapter(
        api_key="secret-key",
        dimension=3,
        retry_backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValueError, match="non-numeric"):
        embedder.embed_query("query text")


@pytest.mark.parametrize(
    ("first_status", "expected_calls"),
    ((429, 2), (500, 2), (400, 1)),
)
def test_dashscope_adapter_retries_only_transient_failures(
    first_status: int,
    expected_calls: int,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(first_status, json={"error": "failure"}, request=request)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]},
            request=request,
        )

    embedder = DashScopeEmbeddingAdapter(
        api_key="secret-key",
        dimension=3,
        max_attempts=2,
        retry_backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    if first_status == 400:
        with pytest.raises(httpx.HTTPStatusError):
            embedder.embed_query("query text")
    else:
        assert embedder.embed_query("query text") == (1.0, 2.0, 3.0)
    assert calls == expected_calls


def test_dashscope_public_config_never_contains_the_api_key() -> None:
    providers = create_retrieval_providers(
        environment={
            "CODECAIRN_EMBEDDING_API_KEY": "do-not-persist",
            "CODECAIRN_EMBEDDING_BASE_URL": "https://workspace.example/compatible-mode/v1/",
        }
    )

    assert "do-not-persist" not in str(providers.public_config)
    assert providers.public_config["embedding"] == {
        "adapter": "dashscope-openai-compatible",
        "adapter_version": "1",
        "model": "text-embedding-v4",
        "source": "https://workspace.example/compatible-mode/v1",
        "revision": "provider-managed",
        "dimension": 1024,
        "license": "Alibaba Cloud Model Studio service",
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
        "primary_candidate_multiplier": 2,
        "secondary_candidate_multiplier": 1,
        "minimum_primary_candidates": 40,
        "minimum_secondary_candidates": 20,
        "neighbor_window": 0,
        "neighbor_snippet_budget": 20,
        "enrichment_order": "matched-adjacency-rerank-top-k-neighbors-v2",
        "matched_facts_per_memory": 3,
        "sibling_facts_per_memory": 2,
    }


def test_recall_mode_rejects_unknown_ablation_names() -> None:
    with pytest.raises(ValueError, match="Unknown recall mode"):
        create_retrieval_providers(environment={"CODECAIRN_RECALL_MODE": "experimental"})


def test_custom_embedding_model_requires_an_explicit_dimension() -> None:
    with pytest.raises(ValueError, match="CODECAIRN_EMBEDDING_DIMENSION"):
        create_retrieval_providers(
            environment={
                "CODECAIRN_RETRIEVAL_PROFILE": "fastembed",
                "CODECAIRN_EMBEDDING_MODEL": "custom/model",
            }
        )


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
        create_retrieval_providers(
            environment={
                "CODECAIRN_RETRIEVAL_PROFILE": "fastembed",
                "CODECAIRN_EMBEDDING_REVISION": "main",
            }
        )


def test_artifact_override_requires_an_explicit_declared_license() -> None:
    with pytest.raises(ValueError, match="CODECAIRN_EMBEDDING_LICENSE"):
        create_retrieval_providers(
            environment={
                "CODECAIRN_RETRIEVAL_PROFILE": "fastembed",
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
    monkeypatch.delenv("TOKENIZERS_PARALLELISM", raising=False)
    monkeypatch.delenv("RAYON_NUM_THREADS", raising=False)

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
                "threads": 1,
                "lazy_load": False,
            },
        ),
        (
            "reranker",
            {
                "model_name": "test/reranker",
                "cache_dir": "/models",
                "specific_model_path": "/snapshots/" + "b" * 40,
                "threads": 1,
                "lazy_load": False,
            },
        ),
    ]
    assert os.environ["TOKENIZERS_PARALLELISM"] == "false"
    assert os.environ["RAYON_NUM_THREADS"] == "1"
