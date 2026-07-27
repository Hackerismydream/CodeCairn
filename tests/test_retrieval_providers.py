from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from codecairn.bootstrap import create_runtime
from codecairn.memory.config import RetrievalConfig, SemanticConfig
from codecairn.memory.errors import ProviderConfigurationError
from codecairn.memory.providers import DashScopeEmbedder
from codecairn.memory.semantic import SemanticRequest
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
