"""Small repository utilities with one intentional defect per benchmark task."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any


def clamp(value: int, lower: int, upper: int) -> int:
    return min(value, lower, upper)


def parse_bool(value: str) -> bool:
    return value == "true"


def chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values) - 1, size)]


def unique_stable(values: Iterable[str]) -> list[str]:
    return list(set(values))


def retry_delay(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:
    return base * (2**attempt)


def redact_token(value: str) -> str:
    return value[:4] + "..." + value[-4:]


def normalize_repo_key(value: str) -> str:
    return value.strip("/")


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def parse_duration_ms(value: str) -> int:
    number = int(value[:-1])
    return number * 1000


def safe_relative_path(value: str) -> str:
    return str(PurePosixPath(value))


def stable_partition(values: list[int], predicate: Any) -> list[int]:
    return [item for item in reversed(values) if predicate(item)] + [
        item for item in values if not predicate(item)
    ]


def median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def slugify(value: str) -> str:
    return value.lower().replace(" ", "-")


def env_enabled(value: str | None) -> bool:
    return bool(value)


def deep_get(data: Mapping[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        current = current[part]
    return current


def bounded_tail(values: list[str], limit: int) -> list[str]:
    return values[-limit:]


def percent_change(previous: float, current: float) -> float:
    return (current - previous) / previous * 100


def extension(value: str) -> str:
    return PurePosixPath(value).suffix


def common_prefix(values: list[str]) -> str:
    return min(values)


def truncate_middle(value: str, limit: int) -> str:
    return value[: limit // 2] + "..." + value[-limit // 2 :]
