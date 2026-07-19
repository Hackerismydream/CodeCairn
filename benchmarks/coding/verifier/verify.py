"""Run one task-specific verifier with only the Python standard library."""

from __future__ import annotations

import sys

import kata


def task_01() -> None:
    assert kata.clamp(-2, 0, 10) == 0
    assert kata.clamp(5, 0, 10) == 5
    assert kata.clamp(12, 0, 10) == 10


def task_02() -> None:
    for value in ("1", "true", "TRUE", "yes", "On"):
        assert kata.parse_bool(value) is True
    for value in ("0", "false", "FALSE", "no", "off", ""):
        assert kata.parse_bool(value) is False


def task_03() -> None:
    assert kata.chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert kata.chunked([], 2) == []
    try:
        kata.chunked([1], 0)
    except ValueError:
        pass
    else:
        raise AssertionError("size zero must fail")


def task_04() -> None:
    assert kata.unique_stable(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def task_05() -> None:
    assert kata.retry_delay(0) == 0.5
    assert kata.retry_delay(3) == 4.0
    assert kata.retry_delay(9) == 8.0


def task_06() -> None:
    assert kata.redact_token("abcdefghijkl") == "abcd...ijkl"
    assert kata.redact_token("abc") == "***"
    assert kata.redact_token("") == ""


def task_07() -> None:
    assert kata.normalize_repo_key(" /Acme//Widgets/ ") == "acme/widgets"
    assert kata.normalize_repo_key("acme/widgets") == "acme/widgets"


def task_08() -> None:
    assert kata.merge_ranges([(4, 7), (1, 3), (3, 4), (10, 11)]) == [(1, 7), (10, 11)]
    assert kata.merge_ranges([]) == []


def task_09() -> None:
    assert kata.parse_duration_ms("12ms") == 12
    assert kata.parse_duration_ms("3s") == 3000
    assert kata.parse_duration_ms("2m") == 120000


def task_10() -> None:
    assert kata.safe_relative_path("src/./pkg/file.py") == "src/pkg/file.py"
    for value in ("../secret", "/absolute", "src/../../secret"):
        try:
            kata.safe_relative_path(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe path accepted: {value}")


def task_11() -> None:
    assert kata.stable_partition([3, 2, 1, 4], lambda item: item % 2 == 0) == [2, 4, 3, 1]


def task_12() -> None:
    assert kata.median([3, 1, 2]) == 2
    assert kata.median([4, 1, 3, 2]) == 2.5
    try:
        kata.median([])
    except ValueError:
        pass
    else:
        raise AssertionError("empty input must fail")


def task_13() -> None:
    assert kata.slugify(" Hello,  Agent_OS! ") == "hello-agent-os"
    assert kata.slugify("already-clean") == "already-clean"


def task_14() -> None:
    for value in ("1", "true", "yes", "on", " TRUE "):
        assert kata.env_enabled(value) is True
    for value in (None, "", "0", "false", "no", "off"):
        assert kata.env_enabled(value) is False


def task_15() -> None:
    data = {"agent": {"limits": {"tokens": 4096}}}
    assert kata.deep_get(data, "agent.limits.tokens") == 4096
    assert kata.deep_get(data, "agent.model", "default") == "default"


def task_16() -> None:
    assert kata.bounded_tail(["a", "b", "c"], 2) == ["b", "c"]
    assert kata.bounded_tail(["a"], 0) == []
    assert kata.bounded_tail(["a"], -1) == []


def task_17() -> None:
    assert kata.percent_change(100, 125) == 25
    assert kata.percent_change(0, 0) == 0
    try:
        kata.percent_change(0, 2)
    except ValueError:
        pass
    else:
        raise AssertionError("undefined non-zero change from zero must fail")


def task_18() -> None:
    assert kata.extension("src/app.py") == ".py"
    assert kata.extension("archive.tar.gz") == ".gz"
    assert kata.extension(".env") == ""
    assert kata.extension("README") == ""


def task_19() -> None:
    assert kata.common_prefix(["agent", "agency", "agenda"]) == "agen"
    assert kata.common_prefix(["x"]) == "x"
    assert kata.common_prefix([]) == ""


def task_20() -> None:
    assert kata.truncate_middle("abcdefghij", 7) == "ab...ij"
    assert kata.truncate_middle("abc", 7) == "abc"
    assert len(kata.truncate_middle("abcdefghij", 7)) == 7


TASKS = {
    "task-01": task_01,
    "task-02": task_02,
    "task-03": task_03,
    "task-04": task_04,
    "task-05": task_05,
    "task-06": task_06,
    "task-07": task_07,
    "task-08": task_08,
    "task-09": task_09,
    "task-10": task_10,
    "task-11": task_11,
    "task-12": task_12,
    "task-13": task_13,
    "task-14": task_14,
    "task-15": task_15,
    "task-16": task_16,
    "task-17": task_17,
    "task-18": task_18,
    "task-19": task_19,
    "task-20": task_20,
}


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in TASKS:
        raise SystemExit("usage: python verify.py task-NN")
    TASKS[sys.argv[1]]()
