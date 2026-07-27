"""OpenAI-compatible untrusted semantic proposal adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx

from codecairn.memory.config import SemanticConfig
from codecairn.memory.errors import ProviderConfigurationError
from codecairn.memory.schema import _record_from_dict, coding_memory_to_dict
from codecairn.memory.semantic import SemanticCandidate, SemanticEvolutionSuggestion, SemanticExtraction, SemanticRequest

_SYSTEM = """Return one JSON object with arrays candidates and evolution.
Candidates may be repository_knowledge, user_preference, or work_state and must
cite only supplied source_fact_ids. Do not author provenance, observed roles,
command outcomes, file changes, or quotes. Evolution may only select keep_both
or supersede. Return JSON only."""


class OpenAISemanticExtractor:
    def __init__(self, config: SemanticConfig, *, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        if config.profile == "none" or config.model is None or config.endpoint is None:
            raise ValueError("Semantic adapter requires an enabled profile")
        endpoint = httpx.URL(config.endpoint)
        if endpoint.scheme != "https" or not endpoint.host or endpoint.userinfo:
            raise ValueError("Semantic endpoint must be HTTPS without credentials")
        self._config = config
        self._configured = bool(api_key)
        self._client = httpx.Client(
            base_url=f"{config.endpoint.rstrip('/')}/",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=60,
            transport=transport,
        )

    def extract(self, request: SemanticRequest) -> SemanticExtraction:
        if not self._configured:
            raise ProviderConfigurationError("Semantic provider key is not configured")
        response = self._client.post(
            "chat/completions",
            json={
                "model": self._config.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task_experience": coding_memory_to_dict(request.task_experience),
                                "allowed_workstream_keys": request.allowed_workstream_keys,
                                "closable_workstream_keys": request.closable_workstream_keys,
                                "active_work_state_heads": request.active_work_state_heads,
                            },
                            sort_keys=True,
                        ),
                    },
                ],
            },
        )
        try:
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError("Semantic provider returned an invalid response") from error
        if not isinstance(data, dict) or set(data) != {"candidates", "evolution"}:
            raise ValueError("Semantic response must contain candidates and evolution")
        return SemanticExtraction(
            extractor_id=f"openai-compatible:{self._config.model}",
            revision="provider-managed",
            candidates=tuple(_candidate(item) for item in _array(data["candidates"])),
            evolution=tuple(_evolution(item) for item in _array(data["evolution"])),
        )


def create_semantic_extractor(config: SemanticConfig, *, environment: Mapping[str, str]) -> OpenAISemanticExtractor | None:
    if config.profile == "none":
        return None
    key = environment.get("CODECAIRN_SEMANTIC_API_KEY", "")
    return OpenAISemanticExtractor(config, api_key=key) if key else None


def _candidate(value: object) -> SemanticCandidate:
    defaults: dict[str, object] = {
        "subject_key": None,
        "claim": None,
        "preference": None,
        "workstream_key": None,
        "workstream_state": None,
        "goal": None,
        "progress": None,
        "blockers": [],
        "next_step": None,
        "terminal_outcome": None,
    }
    if not isinstance(value, dict):
        raise ValueError("Semantic candidate must be an object")
    return _record_from_dict(SemanticCandidate, {**defaults, **value})


def _evolution(value: object) -> SemanticEvolutionSuggestion:
    return _record_from_dict(SemanticEvolutionSuggestion, value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Semantic collection must be an array")
    return value
