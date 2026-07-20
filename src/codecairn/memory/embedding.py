from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Iterable
from itertools import pairwise
from threading import Lock
from typing import Protocol, cast

import httpx

from codecairn.memory.model_artifact import (
    FASTEMBED_INFERENCE_THREADS,
    download_hf_snapshot,
    fastembed_version,
    validate_hf_artifact,
)

VECTOR_DIMENSION = 256
DEFAULT_EMBEDDING_MODEL = "qwen3.7-text-embedding"
DEFAULT_EMBEDDING_SOURCE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_EMBEDDING_LICENSE = "Alibaba Cloud Model Studio service"
DEFAULT_EMBEDDING_DIMENSION = 1024
DEFAULT_EMBEDDING_REVISION = "provider-managed"
DEFAULT_EMBEDDING_BATCH_SIZE = 20
DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 30.0
DEFAULT_EMBEDDING_MAX_ATTEMPTS = 3
DEFAULT_EMBEDDING_RETRY_BACKOFF_SECONDS = 1.0
DASHSCOPE_ADAPTER_VERSION = "1"
DASHSCOPE_QWEN37_DIMENSIONS = frozenset({256, 512, 768, 1024, 1536, 2048, 2560})

DEFAULT_FASTEMBED_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_FASTEMBED_EMBEDDING_SOURCE = "qdrant/bge-small-en-v1.5-onnx-q"
DEFAULT_FASTEMBED_EMBEDDING_LICENSE = "MIT"
DEFAULT_FASTEMBED_EMBEDDING_DIMENSION = 384
DEFAULT_FASTEMBED_EMBEDDING_REVISION = "52398278842ec682c6f32300af41344b1c0b0bb2"
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


class DashScopeEmbeddingAdapter:
    """Synchronous OpenAI-compatible DashScope embedding Adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str = DEFAULT_EMBEDDING_MODEL,
        base_url: str = DEFAULT_EMBEDDING_SOURCE,
        revision: str = DEFAULT_EMBEDDING_REVISION,
        dimension: int = DEFAULT_EMBEDDING_DIMENSION,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
        timeout_seconds: float = DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_EMBEDDING_MAX_ATTEMPTS,
        retry_backoff_seconds: float = DEFAULT_EMBEDDING_RETRY_BACKOFF_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DashScope embedding api_key must not be empty")
        if not model_id.strip():
            raise ValueError("Embedding model ID must not be empty")
        parsed_url = httpx.URL(base_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.host
            or parsed_url.userinfo
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("Embedding base_url must be an HTTPS origin/path without credentials")
        if not revision.strip():
            raise ValueError("Embedding revision must not be empty")
        if dimension < 1:
            raise ValueError("Embedding dimension must be positive")
        if batch_size < 1:
            raise ValueError("Embedding batch size must be positive")
        if batch_size > 20:
            raise ValueError("DashScope embedding batch size must not exceed 20")
        if timeout_seconds <= 0:
            raise ValueError("Embedding timeout must be positive")
        if max_attempts < 1:
            raise ValueError("Embedding max attempts must be positive")
        if retry_backoff_seconds < 0:
            raise ValueError("Embedding retry backoff must not be negative")
        self._model_id = model_id
        self._source_id = base_url.rstrip("/")
        self._revision = revision
        self._dimension = dimension
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._client = httpx.Client(
            base_url=f"{self._source_id}/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

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
            f"dashscope-openai-compatible@{DASHSCOPE_ADAPTER_VERSION}:"
            f"{self._source_id}:{self._model_id}@{self._revision}:{self._dimension}"
        )

    def embed_query(self, text: str) -> tuple[float, ...]:
        if not text.strip():
            raise ValueError("Embedding query must not be empty")
        return self._embed_batch((text,))[0]

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if any(not text.strip() for text in texts):
            raise ValueError("Embedding documents must not contain empty text")
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(self._embed_batch(texts[start : start + self._batch_size]))
        return tuple(vectors)

    def _embed_batch(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        response = self._post(
            {
                "model": self._model_id,
                "input": list(texts),
                "dimensions": self._dimension,
                "encoding_format": "float",
            }
        )
        try:
            raw_body = response.json()
        except ValueError as error:
            raise ValueError("Embedding response is not valid JSON") from error
        if not isinstance(raw_body, dict):
            raise ValueError("Embedding response must be a JSON object")
        body = cast(dict[str, object], raw_body)
        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("Embedding response returned an unexpected vector count")
        indexed: dict[int, tuple[float, ...]] = {}
        for item in data:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("index"), int)
                or isinstance(item.get("index"), bool)
            ):
                raise ValueError("Embedding response item has no valid index")
            index = cast(int, item["index"])
            raw_vector = item.get("embedding")
            if not isinstance(raw_vector, list):
                raise ValueError("Embedding response item has no vector")
            vector = self._vector(raw_vector)
            if index in indexed or index < 0 or index >= len(texts):
                raise ValueError("Embedding response item index is invalid")
            indexed[index] = vector
        if set(indexed) != set(range(len(texts))):
            raise ValueError("Embedding response item indexes are incomplete")
        return tuple(indexed[index] for index in range(len(texts)))

    def _post(self, payload: dict[str, object]) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post("embeddings", json=payload)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                if (status_code != 429 and status_code < 500) or attempt == self._max_attempts:
                    raise
            except httpx.TransportError:
                if attempt == self._max_attempts:
                    raise
            time.sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))
        raise RuntimeError("Embedding retry loop exhausted without a response")

    def _vector(self, value: object) -> tuple[float, ...]:
        items = cast(Iterable[object], value)
        if any(not isinstance(item, int | float) or isinstance(item, bool) for item in items):
            raise ValueError("Embedding response vector contains a non-numeric value")
        try:
            vector = tuple(float(cast(float, item)) for item in cast(Iterable[object], value))
        except (TypeError, ValueError) as error:
            raise ValueError("Embedding response vector contains a non-numeric value") from error
        if len(vector) != self._dimension:
            raise ValueError(
                f"Embedding model {self._model_id} returned {len(vector)} dimensions; "
                f"expected {self._dimension}"
            )
        if any(not math.isfinite(item) for item in vector):
            raise ValueError("Embedding must contain only finite values")
        return vector


class FastEmbedEmbeddingAdapter:
    """Lazy local ONNX embedding Adapter with an explicit model contract."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_FASTEMBED_EMBEDDING_MODEL,
        source_id: str = DEFAULT_FASTEMBED_EMBEDDING_SOURCE,
        revision: str = DEFAULT_FASTEMBED_EMBEDDING_REVISION,
        dimension: int = DEFAULT_FASTEMBED_EMBEDDING_DIMENSION,
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
            threads=FASTEMBED_INFERENCE_THREADS,
            lazy_load=False,
        ),
    )
