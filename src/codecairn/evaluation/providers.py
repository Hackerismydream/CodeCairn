from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from ipaddress import ip_address
from typing import Literal, cast

import httpx

from codecairn.evaluation.model import ModelResponse

ProviderAttemptObserver = Callable[
    [int, Literal["started", "completed", "failed"], str | None], None
]


@dataclass(frozen=True, slots=True)
class TokenPricing:
    """Per-million-token provider pricing recorded in the public run manifest."""

    currency: Literal["CNY", "USD"]
    cached_input_per_million: float
    uncached_input_per_million: float
    output_per_million: float

    def __post_init__(self) -> None:
        if (
            min(
                self.cached_input_per_million,
                self.uncached_input_per_million,
                self.output_per_million,
            )
            < 0
        ):
            raise ValueError("token pricing must not be negative")


_DEEPSEEK_PRICING = {
    "deepseek-v4-flash": TokenPricing(
        currency="CNY",
        cached_input_per_million=0.02,
        uncached_input_per_million=1.0,
        output_per_million=2.0,
    ),
    "deepseek-v4-pro": TokenPricing(
        currency="CNY",
        cached_input_per_million=0.025,
        uncached_input_per_million=3.0,
        output_per_million=6.0,
    ),
}


class OpenAICompatibleTextModel:
    """Small OpenAI-compatible chat-completions adapter without secret persistence."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        send_seed: bool = True,
        thinking: Literal["enabled", "disabled"] | None = None,
        reasoning_effort: Literal["high", "max"] | None = None,
        max_tokens: int | None = None,
        pricing: TokenPricing | None = None,
        pricing_source_url: str | None = None,
        pricing_observed_at: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        parsed_url = httpx.URL(base_url)
        host = parsed_url.host
        if (
            parsed_url.scheme not in {"http", "https"}
            or not host
            or parsed_url.userinfo
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("base_url must be an HTTP origin/path without credentials or query")
        if parsed_url.scheme == "http" and not _is_loopback_host(host):
            raise ValueError("base_url must use HTTPS unless its host is loopback")
        if not model.strip():
            raise ValueError("model must not be empty")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")
        if reasoning_effort is not None and thinking != "enabled":
            raise ValueError("reasoning_effort requires thinking to be enabled")
        if max_tokens is not None and max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if (pricing_source_url is None) != (pricing_observed_at is None):
            raise ValueError("pricing source URL and observation date must be set together")
        self._base_url = base_url.rstrip("/")
        self._trust_env = parsed_url.scheme == "https"
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._send_seed = send_seed
        self._thinking = thinking
        self._reasoning_effort = reasoning_effort
        self._max_tokens = max_tokens
        self._pricing = pricing
        self._pricing_source_url = pricing_source_url
        self._pricing_observed_at = pricing_observed_at
        self._last_provider_attempt_count: ContextVar[int] = ContextVar(
            "last_provider_attempt_count",
            default=0,
        )
        self._provider_attempt_observer: ContextVar[ProviderAttemptObserver | None] = ContextVar(
            "provider_attempt_observer",
            default=None,
        )

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def public_config(self) -> dict[str, object]:
        config: dict[str, object] = {
            "adapter": "openai-compatible-chat-completions",
            "base_url": self._base_url,
            "model": self._model,
            "timeout_seconds": self._timeout_seconds,
            "max_attempts": self._max_attempts,
            "retry_backoff_seconds": self._retry_backoff_seconds,
        }
        if not self._send_seed:
            config["send_seed"] = False
        if self._thinking is not None:
            config["thinking"] = self._thinking
        if self._reasoning_effort is not None:
            config["reasoning_effort"] = self._reasoning_effort
        if self._max_tokens is not None:
            config["max_tokens"] = self._max_tokens
        if self._pricing is not None:
            config["pricing"] = asdict(self._pricing)
        if self._pricing_source_url is not None and self._pricing_observed_at is not None:
            config["pricing_source"] = {
                "url": self._pricing_source_url,
                "observed_at": self._pricing_observed_at,
            }
        return config

    @property
    def last_provider_attempt_count(self) -> int:
        """Return HTTP attempts made by the latest call in this execution context."""

        return self._last_provider_attempt_count.get()

    @contextmanager
    def observe_provider_attempts(
        self,
        observer: ProviderAttemptObserver,
    ) -> Iterator[None]:
        """Expose each transport attempt to the enclosing durable run journal."""

        token = self._provider_attempt_observer.set(observer)
        try:
            yield
        finally:
            self._provider_attempt_observer.reset(token)

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse:
        self._last_provider_attempt_count.set(0)
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        if self._send_seed:
            payload["seed"] = seed
        if self._thinking is not None:
            thinking: dict[str, object] = {"type": self._thinking}
            if self._reasoning_effort is not None:
                thinking["reasoning_effort"] = self._reasoning_effort
            payload["thinking"] = thinking
        if self._max_tokens is not None:
            payload["max_tokens"] = self._max_tokens
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        response = self._post(payload)
        raw_body: object = response.json()
        if not isinstance(raw_body, dict):
            raise ValueError("Model response body must be an object")
        body = cast(dict[str, object], raw_body)
        reported_model = body.get("model")
        if not isinstance(reported_model, str) or not reported_model.strip():
            raise ValueError("Model response has no provider-reported model identity")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Model response has no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ValueError("Model response choice is invalid")
        finish_reason = first.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason != "stop":
            raise RuntimeError(f"Model response ended with finish_reason={finish_reason}")
        message = first.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("Model response content is invalid")
        usage = body.get("usage")
        input_tokens: int | None = None
        output_tokens: int | None = None
        cached_input_tokens: int | None = None
        uncached_input_tokens: int | None = None
        reasoning_tokens: int | None = None
        if isinstance(usage, dict):
            input_tokens = _usage_int(usage, "prompt_tokens")
            output_tokens = _usage_int(usage, "completion_tokens")
            cached_input_tokens = _usage_int(usage, "prompt_cache_hit_tokens")
            uncached_input_tokens = _usage_int(usage, "prompt_cache_miss_tokens")
            completion_details = usage.get("completion_tokens_details")
            if isinstance(completion_details, dict):
                reasoning_tokens = _usage_int(completion_details, "reasoning_tokens")
        if self._pricing is not None:
            priced_usage = (
                input_tokens,
                output_tokens,
                cached_input_tokens,
                uncached_input_tokens,
            )
            if any(value is None for value in priced_usage):
                raise ValueError("Priced model response usage is incomplete")
            assert input_tokens is not None
            assert cached_input_tokens is not None
            assert uncached_input_tokens is not None
            if cached_input_tokens + uncached_input_tokens != input_tokens:
                raise ValueError("Priced model response cache usage does not match input tokens")
        cost_usd, cost_cny = self._calculate_cost(
            cached_input_tokens=cached_input_tokens,
            uncached_input_tokens=uncached_input_tokens,
            output_tokens=output_tokens,
        )
        return ModelResponse(
            text=cast(str, message["content"]),
            model=reported_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            uncached_input_tokens=uncached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_usd=cost_usd,
            cost_cny=cost_cny,
        )

    def _calculate_cost(
        self,
        *,
        cached_input_tokens: int | None,
        uncached_input_tokens: int | None,
        output_tokens: int | None,
    ) -> tuple[float | None, float | None]:
        if (
            self._pricing is None
            or cached_input_tokens is None
            or uncached_input_tokens is None
            or output_tokens is None
        ):
            return None, None
        cost = (
            cached_input_tokens * self._pricing.cached_input_per_million
            + uncached_input_tokens * self._pricing.uncached_input_per_million
            + output_tokens * self._pricing.output_per_million
        ) / 1_000_000
        if self._pricing.currency == "USD":
            return cost, None
        return None, cost

    def _post(self, payload: dict[str, object]) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            self._last_provider_attempt_count.set(attempt)
            self._observe_provider_attempt(attempt, "started", None)
            try:
                response = httpx.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                    timeout=self._timeout_seconds,
                    trust_env=self._trust_env,
                )
                response.raise_for_status()
                self._observe_provider_attempt(attempt, "completed", None)
                return response
            except httpx.HTTPStatusError as error:
                self._observe_provider_attempt(attempt, "failed", type(error).__name__)
                status_code = error.response.status_code
                if status_code != 429 and status_code < 500:
                    raise
                if attempt == self._max_attempts:
                    raise
            except httpx.TransportError as error:
                if _transport_failure_has_unknown_spend(error):
                    # A read/write/protocol failure can happen after the provider accepted the
                    # request. Leave the durable provider attempt start without an outcome so
                    # the enclosing model journal fails closed instead of billing a retry.
                    raise
                self._observe_provider_attempt(attempt, "failed", type(error).__name__)
                if attempt == self._max_attempts:
                    raise
            time.sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))
        raise RuntimeError("Model retry loop exhausted without a response")

    def _observe_provider_attempt(
        self,
        attempt: int,
        status: Literal["started", "completed", "failed"],
        error_type: str | None,
    ) -> None:
        observer = self._provider_attempt_observer.get()
        if observer is not None:
            observer(attempt, status, error_type)


def create_locomo_text_model(
    *,
    role: Literal["answer", "judge", "semantic"],
    environment: Mapping[str, str],
    model_override: str | None = None,
) -> OpenAICompatibleTextModel:
    """Resolve one LoCoMo model role without persisting provider credentials."""

    prefix = f"CODECAIRN_{role.upper()}_"
    deepseek_key = environment.get("DEEPSEEK_API_KEY", "")
    api_key = (
        environment.get(f"{prefix}API_KEY", "")
        or environment.get("CODECAIRN_OPENAI_API_KEY", "")
        or deepseek_key
    )
    profile = (
        environment.get(f"{prefix}PROFILE", "")
        or environment.get("CODECAIRN_PROVIDER_PROFILE", "")
        or ("deepseek" if deepseek_key else "openai-compatible")
    )
    default_base_url = "https://api.deepseek.com" if profile == "deepseek" else ""
    default_model = (
        ("deepseek-v4-flash" if role == "semantic" else "deepseek-v4-pro")
        if profile == "deepseek"
        else ""
    )
    base_url = (
        environment.get(f"{prefix}BASE_URL", "")
        or environment.get("CODECAIRN_OPENAI_BASE_URL", "")
        or default_base_url
    )
    model = (
        model_override
        or environment.get(f"{prefix}MODEL", "")
        or environment.get("CODECAIRN_OPENAI_MODEL", "")
        or default_model
    )
    if not base_url or not api_key or not model:
        raise RuntimeError(
            f"LoCoMo {role} model requires an OpenAI-compatible endpoint, key, and model"
        )
    if profile != "deepseek":
        return OpenAICompatibleTextModel(
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
    pricing = _DEEPSEEK_PRICING.get(model)
    if pricing is None:
        raise ValueError(f"Unsupported DeepSeek model for recorded pricing: {model}")
    thinking = environment.get(
        f"{prefix}THINKING",
        "disabled" if role == "semantic" else "enabled",
    )
    if thinking not in {"enabled", "disabled"}:
        raise ValueError("DeepSeek thinking must be enabled or disabled")
    effort: Literal["high", "max"] | None = None
    if thinking == "enabled":
        raw_effort = environment.get(f"{prefix}REASONING_EFFORT", "high")
        if raw_effort not in {"high", "max"}:
            raise ValueError("DeepSeek reasoning effort must be high or max")
        effort = cast(Literal["high", "max"], raw_effort)
    raw_max_tokens = environment.get(f"{prefix}MAX_TOKENS", "")
    try:
        max_tokens = None if not raw_max_tokens else int(raw_max_tokens)
    except ValueError as error:
        raise ValueError("DeepSeek max tokens must be a positive integer") from error
    if max_tokens is not None and max_tokens < 1:
        raise ValueError("DeepSeek max tokens must be a positive integer")
    return OpenAICompatibleTextModel(
        base_url=base_url,
        api_key=api_key,
        model=model,
        send_seed=False,
        thinking=cast(Literal["enabled", "disabled"], thinking),
        reasoning_effort=effort,
        max_tokens=max_tokens,
        pricing=pricing,
        pricing_source_url="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
        pricing_observed_at="2026-07-23",
    )


def _usage_int(usage: Mapping[str, object], field: str) -> int | None:
    value = usage.get(field)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"Model response usage field {field} is invalid")
    return value


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _transport_failure_has_unknown_spend(error: httpx.TransportError) -> bool:
    """Return whether a transport error may occur after request transmission."""

    return not isinstance(
        error,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.PoolTimeout,
        ),
    )
