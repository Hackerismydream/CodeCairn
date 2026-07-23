from __future__ import annotations

from typing import cast

import httpx
import pytest

from codecairn.evaluation.providers import (
    OpenAICompatibleTextModel,
    TokenPricing,
    create_locomo_text_model,
)


class FakeResponse:
    reported_model = "judge-model"

    def __init__(self, *, reported_model: str | None = None) -> None:
        self._reported_model = reported_model or self.reported_model

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "model": self._reported_model,
            "choices": [{"message": {"content": '{"label":"CORRECT"}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        }


class DeepSeekResponse:
    reported_model = "deepseek-v4-pro"

    def __init__(self, *, reported_model: str | None = None) -> None:
        self._reported_model = reported_model or self.reported_model

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "model": self._reported_model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"label":"CORRECT"}',
                        "reasoning_content": "The answers are equivalent.",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 1_000,
                "prompt_cache_hit_tokens": 600,
                "prompt_cache_miss_tokens": 400,
                "completion_tokens": 200,
                "completion_tokens_details": {"reasoning_tokens": 150},
            },
        }


class TruncatedResponse(DeepSeekResponse):
    def json(self) -> dict[str, object]:
        payload = super().json()
        choices = payload["choices"]
        assert isinstance(choices, list)
        choice = choices[0]
        assert isinstance(choice, dict)
        choice["finish_reason"] = "length"
        return payload


class MissingModelResponse(FakeResponse):
    def json(self) -> dict[str, object]:
        payload = super().json()
        del payload["model"]
        return payload


class IncompletePricedUsageResponse(DeepSeekResponse):
    def json(self) -> dict[str, object]:
        payload = super().json()
        usage = payload["usage"]
        assert isinstance(usage, dict)
        del usage["prompt_cache_miss_tokens"]
        return payload


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
        trust_env: bool,
    ) -> FakeResponse:
        captured.update(
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
            trust_env=trust_env,
        )
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
        "max_attempts": 3,
        "model": "judge-model",
        "retry_backoff_seconds": 1.0,
        "timeout_seconds": 30,
    }
    assert "secret-value" not in str(model.public_config)
    assert cast(dict[str, str], captured["headers"])["Authorization"] == "Bearer secret-value"
    assert captured["trust_env"] is True
    payload = cast(dict[str, object], captured["json"])
    assert payload["seed"] == 19
    assert payload["response_format"] == {"type": "json_object"}


def test_openai_compatible_adapter_propagates_the_provider_reported_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: FakeResponse(reported_model="routed-model-v2"),
    )
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="secret",
        model="configured-model-alias",
    )

    response = model.generate(system="project", user="conversation", seed=19)

    assert response.model == "routed-model-v2"


def test_openai_compatible_adapter_requires_a_provider_reported_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: MissingModelResponse())
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="secret",
        model="configured-model-alias",
    )

    with pytest.raises(ValueError, match="provider-reported model identity"):
        model.generate(system="project", user="conversation", seed=19)


def test_deepseek_profile_preserves_thinking_usage_and_cny_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
        trust_env: bool,
    ) -> DeepSeekResponse:
        captured.update(
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
            trust_env=trust_env,
        )
        return DeepSeekResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    model = OpenAICompatibleTextModel(
        base_url="https://api.deepseek.com",
        api_key="deepseek-secret",
        model="deepseek-v4-pro",
        send_seed=False,
        thinking="enabled",
        reasoning_effort="high",
        pricing=TokenPricing(
            currency="CNY",
            cached_input_per_million=0.025,
            uncached_input_per_million=3.0,
            output_per_million=6.0,
        ),
    )

    response = model.generate(
        system="grade",
        user="question",
        seed=19,
        response_format="json",
    )

    payload = cast(dict[str, object], captured["json"])
    assert "seed" not in payload
    assert payload["thinking"] == {"type": "enabled", "reasoning_effort": "high"}
    assert response.input_tokens == 1_000
    assert response.cached_input_tokens == 600
    assert response.uncached_input_tokens == 400
    assert response.output_tokens == 200
    assert response.reasoning_tokens == 150
    assert response.cost_cny == pytest.approx(0.002415)
    assert response.cost_usd is None
    assert model.public_config["thinking"] == "enabled"
    assert model.public_config["pricing"] == {
        "cached_input_per_million": 0.025,
        "currency": "CNY",
        "output_per_million": 6.0,
        "uncached_input_per_million": 3.0,
    }
    assert "deepseek-secret" not in str(model.public_config)


def test_priced_model_rejects_incomplete_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: IncompletePricedUsageResponse())
    model = OpenAICompatibleTextModel(
        base_url="https://api.deepseek.com",
        api_key="deepseek-secret",
        model="deepseek-v4-pro",
        pricing=TokenPricing(
            currency="CNY",
            cached_input_per_million=0.025,
            uncached_input_per_million=3.0,
            output_per_million=6.0,
        ),
    )

    with pytest.raises(ValueError, match="Priced model response usage is incomplete"):
        model.generate(system="grade", user="question", seed=19)


def test_locomo_roles_resolve_independent_deepseek_models_without_exposing_secrets() -> None:
    environment = {
        "DEEPSEEK_API_KEY": "shared-secret",
        "CODECAIRN_ANSWER_MODEL": "deepseek-v4-pro",
        "CODECAIRN_JUDGE_MODEL": "deepseek-v4-flash",
        "CODECAIRN_JUDGE_API_KEY": "judge-secret",
    }

    answer = create_locomo_text_model(role="answer", environment=environment)
    judge = create_locomo_text_model(role="judge", environment=environment)

    assert answer.public_config["base_url"] == "https://api.deepseek.com"
    assert answer.public_config["model"] == "deepseek-v4-pro"
    assert judge.public_config["model"] == "deepseek-v4-flash"
    assert answer.public_config["thinking"] == "enabled"
    assert judge.public_config["thinking"] == "enabled"
    assert answer.public_config["pricing"] == {
        "cached_input_per_million": 0.025,
        "currency": "CNY",
        "output_per_million": 6.0,
        "uncached_input_per_million": 3.0,
    }
    assert answer.public_config["pricing_source"] == {
        "observed_at": "2026-07-23",
        "url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    }
    assert judge.public_config["pricing"] == {
        "cached_input_per_million": 0.02,
        "currency": "CNY",
        "output_per_million": 2.0,
        "uncached_input_per_million": 1.0,
    }
    public = str({"answer": answer.public_config, "judge": judge.public_config})
    assert "shared-secret" not in public
    assert "judge-secret" not in public


def test_deepseek_role_can_disable_thinking_and_bound_output_tokens() -> None:
    model = create_locomo_text_model(
        role="answer",
        environment={
            "DEEPSEEK_API_KEY": "shared-secret",
            "CODECAIRN_ANSWER_MODEL": "deepseek-v4-flash",
            "CODECAIRN_ANSWER_THINKING": "disabled",
            "CODECAIRN_ANSWER_MAX_TOKENS": "512",
        },
    )

    assert model.public_config["thinking"] == "disabled"
    assert model.public_config["max_tokens"] == 512
    assert "reasoning_effort" not in model.public_config


def test_semantic_projection_role_defaults_to_low_cost_flash_without_thinking() -> None:
    model = create_locomo_text_model(
        role="semantic",
        environment={"DEEPSEEK_API_KEY": "shared-secret"},
    )

    assert model.public_config["model"] == "deepseek-v4-flash"
    assert model.public_config["thinking"] == "disabled"
    assert model.public_config["pricing"] == {
        "cached_input_per_million": 0.02,
        "currency": "CNY",
        "output_per_million": 2.0,
        "uncached_input_per_million": 1.0,
    }


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("CODECAIRN_JUDGE_THINKING", "sometimes", "thinking"),
        ("CODECAIRN_JUDGE_MAX_TOKENS", "zero", "max tokens"),
        ("CODECAIRN_JUDGE_MAX_TOKENS", "0", "max tokens"),
    ],
)
def test_deepseek_role_rejects_invalid_cost_controls(
    name: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        create_locomo_text_model(
            role="judge",
            environment={
                "DEEPSEEK_API_KEY": "shared-secret",
                "CODECAIRN_JUDGE_MODEL": "deepseek-v4-flash",
                name: value,
            },
        )


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


@pytest.mark.parametrize(
    "base_url",
    [
        "http://models.example/v1",
        "http://192.168.1.20/v1",
        "http://169.254.169.254/v1",
    ],
)
def test_model_endpoint_rejects_plaintext_non_loopback_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match=r"HTTPS.*loopback"):
        OpenAICompatibleTextModel(
            base_url=base_url,
            api_key="secret",
            model="model",
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://127.7.8.9:8000/v1",
        "http://[::1]:8000/v1",
    ],
)
def test_model_endpoint_allows_plaintext_only_for_loopback(base_url: str) -> None:
    model = OpenAICompatibleTextModel(
        base_url=base_url,
        api_key="secret",
        model="model",
    )

    assert model.public_config["base_url"] == base_url


def test_plaintext_loopback_requests_ignore_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(*args: object, **kwargs: object) -> FakeResponse:
        captured.update(kwargs)
        return FakeResponse(reported_model="model")

    monkeypatch.setattr(httpx, "post", fake_post)
    model = OpenAICompatibleTextModel(
        base_url="http://127.0.0.1:8000/v1",
        api_key="secret",
        model="model",
    )

    model.generate(system="project", user="conversation", seed=19)

    assert captured["trust_env"] is False


def test_model_retries_pre_request_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def flaky_post(*args: object, **kwargs: object) -> FakeResponse:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("connection was never established")
        return FakeResponse(reported_model="fixed-model")

    monkeypatch.setattr(httpx, "post", flaky_post)
    model = OpenAICompatibleTextModel(
        base_url="https://models.example/v1",
        api_key="secret",
        model="fixed-model",
        max_attempts=3,
        retry_backoff_seconds=0,
    )

    response = model.generate(system="answer", user="question", seed=17)

    assert attempts == 3
    assert response.text == '{"label":"CORRECT"}'


def test_model_rejects_explicitly_incomplete_provider_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: TruncatedResponse())
    model = OpenAICompatibleTextModel(
        base_url="https://api.deepseek.com",
        api_key="secret",
        model="deepseek-v4-pro",
    )

    with pytest.raises(RuntimeError, match="finish_reason=length"):
        model.generate(system="answer", user="question", seed=17)
