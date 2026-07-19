from __future__ import annotations

from typing import cast

import httpx

from codecairn.evaluation.model import ModelResponse


class OpenAICompatibleTextModel:
    """Small OpenAI-compatible chat-completions adapter without secret persistence."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        parsed_url = httpx.URL(base_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.host
            or parsed_url.userinfo
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("base_url must be an HTTP origin/path without credentials or query")
        if not model.strip():
            raise ValueError("model must not be empty")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def public_config(self) -> dict[str, object]:
        return {
            "adapter": "openai-compatible-chat-completions",
            "base_url": self._base_url,
            "model": self._model,
            "timeout_seconds": self._timeout_seconds,
        }

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "seed": seed,
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        body = cast(dict[str, object], response.json())
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Model response has no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ValueError("Model response choice is invalid")
        message = first.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("Model response content is invalid")
        usage = body.get("usage")
        input_tokens: int | None = None
        output_tokens: int | None = None
        if isinstance(usage, dict):
            input_tokens = _optional_int(usage.get("prompt_tokens"))
            output_tokens = _optional_int(usage.get("completion_tokens"))
        return ModelResponse(
            text=cast(str, message["content"]),
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
