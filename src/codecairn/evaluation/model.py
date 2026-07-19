from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    uncached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    cost_cny: float | None = None


class TextModel(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def public_config(self) -> dict[str, object]: ...

    def generate(
        self,
        *,
        system: str,
        user: str,
        seed: int,
        response_format: str = "text",
    ) -> ModelResponse: ...
