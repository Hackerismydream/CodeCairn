from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from codecairn.bootstrap import create_runtime
from codecairn.memory.config import RetrievalConfig, SemanticConfig
from codecairn.memory.errors import ProviderConfigurationError
from codecairn.memory.providers import DashScopeEmbedder, FastEmbedder
from codecairn.memory.schema import RepositoryKnowledgePayload
from codecairn.memory.semantic import SemanticRequest, compile_semantic_extraction
from codecairn.memory.semantic_provider import OpenAISemanticExtractor
from tests.retrieval_fakes import TEST_RETRIEVAL

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"


def test_dashscope_probe_validates_shape_without_leaking_key() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 1_024}]})

    adapter = DashScopeEmbedder(RetrievalConfig.default("dashscope"), api_key="test-key", transport=httpx.MockTransport(respond))
    assert len(adapter.embed_query("one input")) == 1_024


def test_dashscope_missing_key_is_typed_and_wrong_shape_fails_closed() -> None:
    config = RetrievalConfig.default("dashscope")
    with pytest.raises(ProviderConfigurationError):
        DashScopeEmbedder(config, api_key="").embed_query("one input")

    adapter = DashScopeEmbedder(
        config,
        api_key="test-key",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0]}]})),
    )
    with pytest.raises(ValueError, match="invalid embedding response"):
        adapter.embed_query("one input")


def test_fastembed_accepts_its_numpy_float32_output() -> None:
    class FakeFastEmbedding:
        def passage_embed(self, _texts: object) -> tuple[np.ndarray[tuple[int], np.dtype[np.float32]], ...]:
            return (np.zeros(384, dtype=np.float32),)

        def query_embed(self, _query: str) -> tuple[np.ndarray[tuple[int], np.dtype[np.float32]], ...]:
            return (np.zeros(384, dtype=np.float32),)

    adapter = FastEmbedder(RetrievalConfig.default("fastembed"))
    adapter._model = FakeFastEmbedding()  # type: ignore[assignment]

    assert adapter.embed_documents(("one input",)) == (tuple(0.0 for _ in range(384)),)


def test_semantic_adapter_returns_untrusted_fact_id_selection(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime", retrieval_adapters=TEST_RETRIEVAL)
    runtime.import_session(FIXTURE, repo_key="acme/widgets", boundary_kind="manual_finalize")
    memory = runtime.list_memories(repo_key="acme/widgets")[0]
    adapter = OpenAISemanticExtractor(
        SemanticConfig(profile="openai-compatible", model="test-model", endpoint="https://semantic.example/v1"),
        api_key="test-key",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"choices": [{"message": {"content": '{"candidates":[],"evolution":[]}'}}]})
        ),
    )

    result = adapter.extract(SemanticRequest(task_experience=memory, allowed_workstream_keys=()))

    assert result.candidates == ()
    assert result.evolution == ()


def test_semantic_adapter_sends_closed_proposal_contract(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "runtime", retrieval_adapters=TEST_RETRIEVAL)
    runtime.import_session(FIXTURE, repo_key="acme/widgets", boundary_kind="manual_finalize")
    memory = runtime.list_memories(repo_key="acme/widgets")[0]
    source_fact = next(fact for fact in memory.facts if fact.role == "user")
    observed: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        observed.update(payload)
        candidate = {
            "memory_type": "repository_knowledge",
            "title": "Repository deployment region",
            "content": "Deploy this repository in ap-southeast-1.",
            "category": "constraint",
            "source_fact_ids": [source_fact.fact_id],
            "subject_key": "deployment region",
            "claim": "The deployment region is ap-southeast-1.",
            "preference": None,
            "workstream_key": None,
            "workstream_state": None,
            "goal": None,
            "progress": None,
            "blockers": [],
            "next_step": None,
            "terminal_outcome": None,
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"candidates": [candidate], "evolution": []}, separators=(",", ":"))}}]
            },
        )

    adapter = OpenAISemanticExtractor(
        SemanticConfig(profile="openai-compatible", model="test-model", endpoint="https://semantic.example/v1"),
        api_key="test-key",
        transport=httpx.MockTransport(respond),
    )

    result = adapter.extract(SemanticRequest(task_experience=memory, allowed_workstream_keys=()))

    messages = observed["messages"]
    assert isinstance(messages, list)
    system = messages[0]["content"]
    user = json.loads(messages[1]["content"])
    assert "Every candidate object must contain exactly these 15 fields" in system
    assert "repository_knowledge" in system
    assert "user_preference" in system
    assert "work_state" in system
    assert user["allowed_source_fact_ids"] == sorted(fact.fact_id for fact in memory.facts)
    assert user["user_source_fact_ids"] == [source_fact.fact_id]
    assert result.revision == "codecairn-semantic-proposal-v2"
    assert result.candidates[0].subject_key == "deployment region"
    compiled = compile_semantic_extraction(result, SemanticRequest(task_experience=memory, allowed_workstream_keys=()))
    assert isinstance(compiled.memories[0].payload, RepositoryKnowledgePayload)
    assert compiled.memories[0].payload.claim == "The deployment region is ap-southeast-1."
