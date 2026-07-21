from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from codecairn.memory.embedding import DASHSCOPE_ADAPTER_VERSION, EmbeddingProvider
from codecairn.memory.model_artifact import (
    FASTEMBED_INFERENCE_THREADS,
    FASTEMBED_TOKENIZER_PARALLELISM,
    FASTEMBED_TOKENIZER_THREADS,
    fastembed_version,
)
from codecairn.memory.recall_planner import RecallPlannerConfig
from codecairn.memory.reranking import RerankingProvider

RetrievalProfile = Literal["dashscope", "fastembed", "hashing-test"]


@dataclass(frozen=True, slots=True)
class RetrievalProviders:
    """One immutable embedding and reranking configuration shared by a runtime."""

    profile: RetrievalProfile
    embedder: EmbeddingProvider
    reranker: RerankingProvider
    embedding_license: str
    reranker_license: str
    planner: RecallPlannerConfig = field(default_factory=RecallPlannerConfig)

    @property
    def config_sha256(self) -> str:
        return retrieval_config_sha256(self.public_config)

    @property
    def public_config(self) -> dict[str, object]:
        if self.profile == "hashing-test":
            return {
                "method": "hybrid-rrf-test-adapters",
                "embedding": {
                    "adapter": "hashing-test",
                    "model": self.embedder.model_id,
                    "source": self.embedder.source_id,
                    "revision": self.embedder.revision,
                    "dimension": self.embedder.dimension,
                    "license": self.embedding_license,
                },
                "reranker": {
                    "adapter": "fusion-score-test",
                    "model": self.reranker.model_id,
                    "source": self.reranker.source_id,
                    "revision": self.reranker.revision,
                    "license": self.reranker_license,
                },
                "planner": self.planner.public_config,
            }
        embedding: dict[str, object]
        if self.profile == "dashscope":
            embedding = {
                "adapter": "dashscope-openai-compatible",
                "adapter_version": DASHSCOPE_ADAPTER_VERSION,
                "model": self.embedder.model_id,
                "source": self.embedder.source_id,
                "revision": self.embedder.revision,
                "dimension": self.embedder.dimension,
                "license": self.embedding_license,
            }
        else:
            embedding = {
                "adapter": "fastembed",
                "adapter_version": fastembed_version(),
                "adapter_license": "Apache-2.0",
                "model": self.embedder.model_id,
                "source": self.embedder.source_id,
                "revision": self.embedder.revision,
                "dimension": self.embedder.dimension,
                "license": self.embedding_license,
            }
        return {
            "method": "hybrid-rrf-cross-encoder",
            "inference_threads": FASTEMBED_INFERENCE_THREADS,
            "tokenizer_parallelism": FASTEMBED_TOKENIZER_PARALLELISM,
            "tokenizer_threads": FASTEMBED_TOKENIZER_THREADS,
            "embedding": embedding,
            "reranker": {
                "adapter": "fastembed-cross-encoder",
                "adapter_version": fastembed_version(),
                "adapter_license": "Apache-2.0",
                "model": self.reranker.model_id,
                "source": self.reranker.source_id,
                "revision": self.reranker.revision,
                "license": self.reranker_license,
                "batch_size": self.reranker.batch_size,
            },
            "planner": self.planner.public_config,
        }


def retrieval_config_sha256(config: dict[str, object]) -> str:
    canonical = json.dumps(
        config,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
