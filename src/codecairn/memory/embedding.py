from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from itertools import pairwise
from threading import Lock
from typing import Protocol, cast

from codecairn.memory.model_artifact import (
    download_hf_snapshot,
    fastembed_version,
    validate_hf_artifact,
)

VECTOR_DIMENSION = 256
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_EMBEDDING_SOURCE = "qdrant/bge-small-en-v1.5-onnx-q"
DEFAULT_EMBEDDING_LICENSE = "MIT"
DEFAULT_EMBEDDING_DIMENSION = 384
DEFAULT_EMBEDDING_REVISION = "52398278842ec682c6f32300af41344b1c0b0bb2"
_TOKEN_PATTERN = re.compile(r"[a-z0-9_./-]+|[^\W\s]", re.IGNORECASE)


class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def source_id(self) -> str: ...

    @property
    def revision(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    @property
    def index_identity(self) -> str: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class _FastEmbedModel(Protocol):
    def query_embed(self, query: str) -> Iterable[object]: ...

    def passage_embed(self, texts: Iterable[str]) -> Iterable[object]: ...


class HashingEmbedder:
    """Deterministic test Adapter; production composition uses a learned model."""

    model_id = "test/hashing-sha256-v1"
    source_id = "builtin/hashing-sha256-v1"
    revision = "test-v1"
    dimension = VECTOR_DIMENSION
    index_identity = f"hashing-test:{source_id}@{revision}:{dimension}"

    def embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * VECTOR_DIMENSION
        tokens = [match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(text)]
        features = tokens + [f"{left}\x00{right}" for left, right in pairwise(tokens)]
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            position = int.from_bytes(digest[:2], "big") % VECTOR_DIMENSION
            vector[position] += 1.0 if digest[2] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return tuple(vector)
        return tuple(value / norm for value in vector)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self.embed(text)

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.embed(text) for text in texts)


class FastEmbedEmbeddingAdapter:
    """Lazy local ONNX embedding Adapter with an explicit model contract."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_EMBEDDING_MODEL,
        source_id: str = DEFAULT_EMBEDDING_SOURCE,
        revision: str = DEFAULT_EMBEDDING_REVISION,
        dimension: int = DEFAULT_EMBEDDING_DIMENSION,
        cache_dir: str | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("Embedding model ID must not be empty")
        validate_hf_artifact(source_id=source_id, revision=revision)
        if dimension < 1:
            raise ValueError("Embedding dimension must be positive")
        self._model_id = model_id
        self._source_id = source_id
        self._revision = revision
        self._dimension = dimension
        self._cache_dir = cache_dir
        self._model: _FastEmbedModel | None = None
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

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def index_identity(self) -> str:
        return (
            f"fastembed@{fastembed_version()}:{self._source_id}@{self._revision}:{self._dimension}"
        )

    def embed_query(self, text: str) -> tuple[float, ...]:
        if not text.strip():
            raise ValueError("Embedding query must not be empty")
        values = tuple(self._model_instance().query_embed(text))
        if len(values) != 1:
            raise ValueError("Embedding model did not return exactly one query vector")
        return self._vector(values[0])

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if any(not text.strip() for text in texts):
            raise ValueError("Embedding documents must not contain empty text")
        if not texts:
            return ()
        values = tuple(self._model_instance().passage_embed(texts))
        if len(values) != len(texts):
            raise ValueError("Embedding model returned an unexpected vector count")
        return tuple(self._vector(value) for value in values)

    def _model_instance(self) -> _FastEmbedModel:
        with self._lock:
            if self._model is None:
                self._model = _load_fastembed_model(
                    self._model_id,
                    self._source_id,
                    self._revision,
                    self._cache_dir,
                )
            return self._model

    def _vector(self, value: object) -> tuple[float, ...]:
        vector = tuple(float(cast(float, item)) for item in cast(Iterable[object], value))
        if len(vector) != self._dimension:
            raise ValueError(
                f"Embedding model {self._model_id} returned {len(vector)} dimensions; "
                f"expected {self._dimension}"
            )
        if any(not math.isfinite(item) for item in vector):
            raise ValueError("Embedding model returned a non-finite value")
        return vector


def _load_fastembed_model(
    model_id: str,
    source_id: str,
    revision: str,
    cache_dir: str | None,
) -> _FastEmbedModel:
    from fastembed import TextEmbedding

    snapshot = download_hf_snapshot(
        source_id=source_id,
        revision=revision,
        cache_dir=cache_dir,
    )
    return cast(
        _FastEmbedModel,
        TextEmbedding(
            model_name=model_id,
            cache_dir=cache_dir,
            specific_model_path=snapshot,
            lazy_load=False,
        ),
    )
