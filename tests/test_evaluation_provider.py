from __future__ import annotations

from typing import cast

import httpx
import pytest

from codecairn.evaluation.providers import OpenAICompatibleTextModel


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "choices": [{"message": {"content": '{"label":"CORRECT"}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }


def test_openai_compatible_adapter_keeps_secrets_out_of_public_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> FakeResponse:
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="secret-value",
        model="judge-model",
        timeout_seconds=30,
    )

    response = model.generate(
        system="grade",
        user="question",
        seed=19,
        response_format="json",
    )

    assert response.text == '{"label":"CORRECT"}'
    assert response.input_tokens == 12
    assert response.output_tokens == 4
    assert model.public_config == {
        "adapter": "openai-compatible-chat-completions",
        "base_url": "https://models.example/v1",
        "model": "judge-model",
        "timeout_seconds": 30,
    }
    assert "secret-value" not in str(model.public_config)
    assert cast(dict[str, str], captured["headers"])["Authorization"] == "Bearer secret-value"
    payload = cast(dict[str, object], captured["json"])
    assert payload["seed"] == 19
    assert payload["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    "base_url",
    [
        "https://secret@example.com/v1",
        "https://example.com/v1?api_key=secret",
        "file:///tmp/model",
    ],
)
def test_model_endpoint_rejects_credential_bearing_or_non_http_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleTextModel(
            base_url=base_url,
            api_key="secret",
            model="model",
        )
