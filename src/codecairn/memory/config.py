"""Closed value objects for installed runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from codecairn.memory.errors import ConfigurationError
from codecairn.memory.schema import canonical_json

RetrievalProfile = Literal["dashscope", "fastembed"]

DASHSCOPE_MODEL = "qwen3.7-text-embedding"
DASHSCOPE_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
FASTEMBED_SOURCE = "qdrant/bge-small-en-v1.5-onnx-q"
FASTEMBED_REVISION = "52398278842ec682c6f32300af41344b1c0b0bb2"
RERANKER_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
RERANKER_REVISION = "a09144355adeed5f58c8ed011d209bf8ee5a1fec"


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    profile: RetrievalProfile
    model: str
    dimension: int
    endpoint: str | None
    revision: str
    cache_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.profile not in {"dashscope", "fastembed"}:
            raise ConfigurationError(f"Unknown retrieval profile: {self.profile}")
        if not self.model or not self.revision or self.dimension < 1:
            raise ConfigurationError("Retrieval model, revision, and dimension are required")
        if self.profile == "dashscope" and (
            self.model != DASHSCOPE_MODEL or self.dimension != 1_024 or not self.endpoint
        ):
            raise ConfigurationError("DashScope requires qwen3.7-text-embedding at 1024 dimensions")
        if self.profile == "fastembed" and (
            self.model != FASTEMBED_MODEL or self.dimension != 384 or self.endpoint is not None
        ):
            raise ConfigurationError("FastEmbed requires the pinned 384-dimension local profile")

    @classmethod
    def default(cls, profile: RetrievalProfile) -> RetrievalConfig:
        if profile == "dashscope":
            return cls(
                profile=profile,
                model=DASHSCOPE_MODEL,
                dimension=1_024,
                endpoint=DASHSCOPE_ENDPOINT,
                revision="provider-managed",
            )
        return cls(
            profile=profile,
            model=FASTEMBED_MODEL,
            dimension=384,
            endpoint=None,
            revision=FASTEMBED_REVISION,
        )

    @property
    def public_config(self) -> dict[str, object]:
        return {
            "adapter_version": "codecairn-retrieval-v1",
            "profile": self.profile,
            "model": self.model,
            "dimension": self.dimension,
            "endpoint": self.endpoint,
            "revision": self.revision,
            "reranker_model": RERANKER_MODEL,
            "reranker_revision": RERANKER_REVISION,
        }

    @property
    def index_identity(self) -> str:
        import hashlib

        return hashlib.sha256(canonical_json(self.public_config).encode()).hexdigest()

    @property
    def network(self) -> bool:
        return self.profile == "dashscope"


@dataclass(frozen=True, slots=True)
class SemanticConfig:
    profile: str = "none"
    model: str | None = None
    endpoint: str | None = None

    def __post_init__(self) -> None:
        if not self.profile:
            raise ConfigurationError("Semantic profile must not be empty")
        if self.profile == "none" and (self.model is not None or self.endpoint is not None):
            raise ConfigurationError("Disabled semantic capture cannot name a model or endpoint")
        if self.profile != "none" and (not self.model or not self.endpoint):
            raise ConfigurationError("Enabled semantic capture requires a model and endpoint")

    @property
    def network(self) -> bool:
        return self.profile != "none"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    runtime_root: Path
    repo_key: str
    binding_path: Path
    retrieval: RetrievalConfig
    semantic: SemanticConfig

    def __post_init__(self) -> None:
        if not self.repo_key or len(self.repo_key.encode()) > 512:
            raise ConfigurationError("Repository key must contain 1..512 UTF-8 bytes")
