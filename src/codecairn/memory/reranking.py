from __future__ import annotations

import math
from collections.abc import Iterable
from threading import Lock
from typing import Protocol, cast

from codecairn.memory.model_artifact import (
    FASTEMBED_INFERENCE_THREADS,
    download_hf_snapshot,
    validate_hf_artifact,
)
from codecairn.memory.models import RerankDocument, RerankScore

DEFAULT_RERANKER_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
DEFAULT_RERANKER_SOURCE = "Xenova/ms-marco-MiniLM-L-6-v2"
DEFAULT_RERANKER_LICENSE = "Apache-2.0"
DEFAULT_RERANKER_REVISION = "a09144355adeed5f58c8ed011d209bf8ee5a1fec"


class RerankingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def source_id(self) -> str: ...

    @property
    def revision(self) -> str: ...

    def rerank(
        self,
        query: str,
        documents: tuple[RerankDocument, ...],
    ) -> tuple[RerankScore, ...]: ...


class FusionScoreRerankingAdapter:
    """Deterministic test Adapter that preserves reciprocal-rank fusion order."""

    model_id = "test/rrf-score-v1"
    source_id = "builtin/rrf-score-v1"
    revision = "test-v1"

    def rerank(
        self,
        query: str,
        documents: tuple[RerankDocument, ...],
    ) -> tuple[RerankScore, ...]:
        return tuple(
            RerankScore(memory_id=document.memory_id, score=document.fusion_score)
            for document in documents
        )


class _FastEmbedReranker(Protocol):
    def rerank(self, query: str, documents: Iterable[str]) -> Iterable[float]: ...


class FastEmbedRerankingAdapter:
    """Lazy local ONNX CrossEncoder Adapter that returns raw relevance logits."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_RERANKER_MODEL,
        source_id: str = DEFAULT_RERANKER_SOURCE,
        revision: str = DEFAULT_RERANKER_REVISION,
        cache_dir: str | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("Reranker model ID must not be empty")
        validate_hf_artifact(source_id=source_id, revision=revision)
        self._model_id = model_id
        self._source_id = source_id
        self._revision = revision
        self._cache_dir = cache_dir
        self._model: _FastEmbedReranker | None = None
        self._lock = Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def revision(self) -> str:
        return self._revision

    def rerank(
        self,
        query: str,
        documents: tuple[RerankDocument, ...],
    ) -> tuple[RerankScore, ...]:
        if not query.strip():
            raise ValueError("Reranker query must not be empty")
        if not documents:
            return ()
        scores = tuple(
            float(score)
            for score in self._model_instance().rerank(
                query,
                (document.text for document in documents),
            )
        )
        if len(scores) != len(documents):
            raise ValueError("Reranker returned an unexpected score count")
        if any(not math.isfinite(score) for score in scores):
            raise ValueError("Reranker returned a non-finite score")
        return tuple(
            RerankScore(memory_id=document.memory_id, score=score)
            for document, score in zip(documents, scores, strict=True)
        )

    def _model_instance(self) -> _FastEmbedReranker:
        with self._lock:
            if self._model is None:
                self._model = _load_fastembed_reranker(
                    self._model_id,
                    self._source_id,
                    self._revision,
                    self._cache_dir,
                )
            return self._model


def _load_fastembed_reranker(
    model_id: str,
    source_id: str,
    revision: str,
    cache_dir: str | None,
) -> _FastEmbedReranker:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    snapshot = download_hf_snapshot(
        source_id=source_id,
        revision=revision,
        cache_dir=cache_dir,
    )
    return cast(
        _FastEmbedReranker,
        TextCrossEncoder(
            model_name=model_id,
            cache_dir=cache_dir,
            specific_model_path=snapshot,
            threads=FASTEMBED_INFERENCE_THREADS,
            lazy_load=False,
        ),
    )
