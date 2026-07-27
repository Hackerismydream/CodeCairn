"""OpenAI-compatible untrusted semantic proposal adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal, cast

import httpx

from codecairn.memory.config import SemanticConfig
from codecairn.memory.errors import ProviderConfigurationError
from codecairn.memory.schema import coding_memory_to_dict
from codecairn.memory.semantic import (
    SemanticCandidate,
    SemanticEvolutionSuggestion,
    SemanticExtraction,
    SemanticMemoryType,
    SemanticRequest,
)

_SYSTEM = """Return one JSON object with arrays candidates and evolution.
Candidates may be repository_knowledge, user_preference, or work_state and must
cite only supplied source_fact_ids. Do not author provenance, observed roles,
command outcomes, file changes, or quotes. Evolution may only select keep_both
or supersede. Return JSON only."""


class OpenAISemanticExtractor:
    def __init__(
        self,
        config: SemanticConfig,
        *,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
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


def create_semantic_extractor(
    config: SemanticConfig,
    *,
    environment: Mapping[str, str],
) -> OpenAISemanticExtractor | None:
    if config.profile == "none":
        return None
    key = environment.get("CODECAIRN_SEMANTIC_API_KEY", "")
    return OpenAISemanticExtractor(config, api_key=key) if key else None


def _candidate(value: object) -> SemanticCandidate:
    data = _object(value)
    allowed = {
        "memory_type",
        "title",
        "content",
        "category",
        "source_fact_ids",
        "subject_key",
        "claim",
        "preference",
        "workstream_key",
        "workstream_state",
        "goal",
        "progress",
        "blockers",
        "next_step",
        "terminal_outcome",
    }
    if not set(data).issubset(allowed):
        raise ValueError("Semantic candidate contains unknown fields")
    return SemanticCandidate(
        memory_type=cast(SemanticMemoryType, data.get("memory_type")),
        title=_required_string(data, "title"),
        content=_required_string(data, "content"),
        category=_required_string(data, "category"),
        source_fact_ids=_strings(data.get("source_fact_ids")),
        subject_key=_optional_string(data.get("subject_key")),
        claim=_optional_string(data.get("claim")),
        preference=_optional_string(data.get("preference")),
        workstream_key=_optional_string(data.get("workstream_key")),
        workstream_state=cast(
            Literal["open", "closed"] | None,
            data.get("workstream_state"),
        ),
        goal=_optional_string(data.get("goal")),
        progress=_optional_string(data.get("progress")),
        blockers=_strings(data.get("blockers", [])),
        next_step=_optional_string(data.get("next_step")),
        terminal_outcome=_optional_string(data.get("terminal_outcome")),
    )


def _evolution(value: object) -> SemanticEvolutionSuggestion:
    data = _object(value)
    if set(data) != {
        "decision",
        "relation_kind",
        "predecessor_id",
        "successor_candidate_index",
        "supporting_fact_ids",
        "reason",
    }:
        raise ValueError("Semantic evolution has invalid fields")
    index = data["successor_candidate_index"]
    if not isinstance(index, int) or isinstance(index, bool):
        raise ValueError("Semantic successor index must be an integer")
    return SemanticEvolutionSuggestion(
        decision=cast(Any, data["decision"]),
        relation_kind=cast(Any, data["relation_kind"]),
        predecessor_id=_optional_string(data["predecessor_id"]),
        successor_candidate_index=index,
        supporting_fact_ids=_strings(data["supporting_fact_ids"]),
        reason=_required_string(data, "reason"),
    )


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Semantic item must be an object")
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Semantic collection must be an array")
    return value


def _strings(value: object) -> tuple[str, ...]:
    values = _array(value)
    if not all(isinstance(item, str) for item in values):
        raise ValueError("Semantic string collection is invalid")
    return tuple(cast(str, item) for item in values)


def _required_string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Semantic {key} must be a string")
    return value


def _optional_string(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError("Semantic optional value must be a string or null")
    return value
