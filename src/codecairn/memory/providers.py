"""Fail-closed production embedding and reranking adapters."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Iterable
from numbers import Real
from pathlib import Path
from threading import Lock
from typing import Protocol, cast

import httpx

from codecairn.memory.config import FASTEMBED_SOURCE, RERANKER_MODEL, RERANKER_REVISION, RetrievalConfig
from codecairn.memory.errors import ProviderConfigurationError


class _FastEmbedding(Protocol):
    def query_embed(self, query: str) -> Iterable[object]: ...

    def passage_embed(self, texts: Iterable[str]) -> Iterable[object]: ...


class _FastReranker(Protocol):
    def rerank(self, query: str, documents: Iterable[str], batch_size: int) -> Iterable[float]: ...


class DashScopeEmbedder:
    def __init__(self, config: RetrievalConfig, *, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        if config.profile != "dashscope":
            raise ValueError("DashScope adapter requires the dashscope profile")
        endpoint = httpx.URL(cast(str, config.endpoint))
        if endpoint.scheme != "https" or not endpoint.host or endpoint.userinfo:
            raise ValueError("DashScope endpoint must be HTTPS without credentials")
        self._config = config
        self._client = httpx.Client(
            base_url=f"{config.endpoint}/",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=30,
            transport=transport,
        )
        self._configured = bool(api_key)

    model_id = property(lambda self: self._config.model)
    source_id = property(lambda self: cast(str, self._config.endpoint))
    revision = property(lambda self: self._config.revision)
    dimension = property(lambda self: self._config.dimension)
    index_identity = property(lambda self: self._config.index_identity)
    relevance_threshold = property(lambda self: self._config.relevance_threshold)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._embed((text,))[0]

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(vector for start in range(0, len(texts), 10) for vector in self._embed(texts[start : start + 10]))

    def _embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not self._configured:
            raise ProviderConfigurationError("DashScope embedding key is not configured")
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding input must contain non-empty text")
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = self._client.post(
                    "embeddings",
                    json={"model": self.model_id, "input": list(texts), "dimensions": self.dimension, "encoding_format": "float"},
                )
                response.raise_for_status()
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                if attempt == 2:
                    raise ProviderConfigurationError("DashScope embedding is unreachable") from None
                time.sleep(0.25 * 2**attempt)
            except httpx.HTTPStatusError as error:
                raise ProviderConfigurationError(f"DashScope embedding returned HTTP {error.response.status_code}") from None
        assert response is not None
        try:
            body = response.json()
            data = body["data"]
            indexed = {item["index"]: _vector(item["embedding"], dimension=self.dimension) for item in data}
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("DashScope returned an invalid embedding response") from error
        if set(indexed) != set(range(len(texts))):
            raise ValueError("DashScope returned incomplete embedding indexes")
        return tuple(indexed[index] for index in range(len(texts)))


class FastEmbedder:
    def __init__(self, config: RetrievalConfig) -> None:
        if config.profile != "fastembed":
            raise ValueError("FastEmbed adapter requires the fastembed profile")
        self._config = config
        self._model: _FastEmbedding | None = None
        self._lock = Lock()

    model_id = property(lambda self: self._config.model)
    source_id = property(lambda self: FASTEMBED_SOURCE)
    revision = property(lambda self: self._config.revision)
    dimension = property(lambda self: self._config.dimension)
    index_identity = property(lambda self: self._config.index_identity)
    relevance_threshold = property(lambda self: self._config.relevance_threshold)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return _vector(next(iter(self._instance().query_embed(text))), dimension=self.dimension)

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(_vector(vector, dimension=self.dimension) for vector in self._instance().passage_embed(texts))

    def _instance(self) -> _FastEmbedding:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                snapshot = _snapshot(FASTEMBED_SOURCE, self.revision, self._config.cache_dir)
                self._model = cast(
                    _FastEmbedding,
                    TextEmbedding(
                        model_name=self.model_id,
                        cache_dir=str(self._config.cache_dir) if self._config.cache_dir else None,
                        specific_model_path=snapshot,
                        threads=2,
                        lazy_load=False,
                    ),
                )
            return self._model


class FastEmbedReranker:
    model_id = RERANKER_MODEL

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        self._model: _FastReranker | None = None
        self._lock = Lock()

    def rerank(self, query: str, documents: tuple[tuple[str, str, float], ...]) -> tuple[tuple[str, float], ...]:
        if not documents:
            return ()
        scores = tuple(
            float(value) for value in self._instance().rerank(query, (text for _memory_id, text, _score in documents), batch_size=8)
        )
        if len(scores) != len(documents) or any(not math.isfinite(score) for score in scores):
            raise ValueError("Reranker returned invalid scores")
        return tuple((memory_id, score) for (memory_id, _text, _fusion), score in zip(documents, scores, strict=True))

    def _instance(self) -> _FastReranker:
        with self._lock:
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
                snapshot = _snapshot(RERANKER_MODEL, RERANKER_REVISION, self._cache_dir)
                self._model = cast(
                    _FastReranker,
                    TextCrossEncoder(
                        model_name=RERANKER_MODEL,
                        cache_dir=str(self._cache_dir) if self._cache_dir else None,
                        specific_model_path=snapshot,
                        threads=2,
                        lazy_load=False,
                    ),
                )
            return self._model


def create_retrieval_adapters(
    config: RetrievalConfig, *, environment: dict[str, str] | os._Environ[str] | None = None
) -> tuple[DashScopeEmbedder | FastEmbedder, FastEmbedReranker]:
    env = os.environ if environment is None else environment
    embedder: DashScopeEmbedder | FastEmbedder = (
        DashScopeEmbedder(config, api_key=env.get("CODECAIRN_EMBEDDING_API_KEY") or env.get("DASHSCOPE_API_KEY", ""))
        if config.profile == "dashscope"
        else FastEmbedder(config)
    )
    return embedder, FastEmbedReranker(config.cache_dir)


def _snapshot(source: str, revision: str, cache_dir: Path | None) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id=source, revision=revision, cache_dir=str(cache_dir) if cache_dir else None)


def _vector(value: object, *, dimension: int) -> tuple[float, ...]:
    try:
        items = tuple(cast(Iterable[object], value))
        if any(not isinstance(item, Real) or isinstance(item, bool) for item in items):
            raise ValueError
        vector = tuple(float(cast(Real, item)) for item in items)
    except (TypeError, ValueError) as error:
        raise ValueError("Embedding contains a non-numeric value") from error
    if len(vector) != dimension or any(not math.isfinite(item) for item in vector):
        raise ValueError(f"Embedding must contain {dimension} finite values")
    return vector
