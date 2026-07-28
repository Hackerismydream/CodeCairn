from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from codecairn.evaluation.artifacts import file_sha256
from codecairn.evaluation.gates import _synthetic_session, paid_plan, run_retrieval
from codecairn.evaluation.locomo import (
    Question,
    TextProvider,
    _preflight_recall,
    _stable_id,
    _worker_count,
    compose_repair,
    load_selection,
    report_locomo,
    write_repair_selection,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def test_retrieval_gate_runs_current_recall_and_meets_release_thresholds(tmp_path: Path) -> None:
    result = run_retrieval(output_root=tmp_path, run_id=None)
    aggregate = result["aggregate"]
    assert isinstance(aggregate, dict)
    assert aggregate["query_count"] == 100
    assert aggregate["recall_at_5"] >= 0.9
    assert aggregate["provenance_coverage"] == 1
    assert aggregate["stale_predecessor_leakage"] == 0
    assert aggregate["p95_latency_ms"] <= 4_000


def test_scale_generator_emits_exactly_100_events_for_both_clients() -> None:
    for provider in ("codex", "claude"):
        records = [json.loads(line) for line in _synthetic_session(provider, 7).splitlines()]
        assert len(records) == 100


def test_locomo_selection_report_and_exact_repair_are_pure(tmp_path: Path) -> None:
    dataset = [
        {
            "sample_id": "sample_1",
            "conversation": {
                "speaker_a": "A",
                "speaker_b": "B",
                "session_1_date_time": "1:00 PM on 1 January, 2025",
                "session_1": [{"dia_id": "d1", "speaker": "A", "text": "The release is Friday."}],
            },
            "qa": [
                {"question": "When is release?", "answer": "Friday", "category": 1},
                {"question": "Who said it?", "answer": "A", "category": 2},
            ],
        }
    ]
    dataset_path = tmp_path / "dataset.json"
    _write_json(dataset_path, dataset)
    ids = [
        _stable_id("locomo-question", "sample_1", "0", "When is release?"),
        _stable_id("locomo-question", "sample_1", "1", "Who said it?"),
    ]
    question_set = {
        "schema_version": 1,
        "selection_id": "tiny",
        "dataset_sha256": file_sha256(dataset_path),
        "algorithm": "explicit-question-ids-v1",
        "seed": "tiny",
        "category_targets": {"1": 1, "2": 1},
        "question_ids": ids,
        "selection_sha256": hashlib.sha256(json.dumps(sorted(ids), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
    }
    selection_path = tmp_path / "selection.json"
    _write_json(selection_path, question_set)
    selection = load_selection(dataset_path, selection_path)
    assert len(selection.questions) == 2
    assert len(selection.sessions) == 1

    shared = {
        "schema_version": 1,
        "protocol": "codecairn-locomo-v01",
        "dataset_sha256": file_sha256(dataset_path),
        "providers": {"answer": "a", "judge": "j", "retrieval": "r"},
    }
    base = tmp_path / "base"
    _write_json(base / "manifest.json", {**shared, "question_set": {"question_count": 2}})
    _write_json(
        base / "questions" / f"{ids[0]}.json", {"question_id": ids[0], "category": 1, "outcome": "correct", "retrieval_latency_ms": 1.0}
    )
    _write_json(base / "questions" / f"{ids[1]}.json", {"question_id": ids[1], "category": 2, "outcome": "infrastructure_failure"})
    assert report_locomo(base)["infrastructure_failed_count"] == 1
    repair_selection = write_repair_selection(base, tmp_path / "repair-selection.json")
    assert repair_selection["question_ids"] == [ids[1]]

    repair = tmp_path / "repair"
    _write_json(repair / "manifest.json", {**shared, "question_set": {"question_count": 1}})
    _write_json(
        repair / "questions" / f"{ids[1]}.json", {"question_id": ids[1], "category": 2, "outcome": "wrong", "retrieval_latency_ms": 2.0}
    )
    composite = compose_repair(base, repair, tmp_path / "composite.json")
    aggregate = composite["aggregate"]
    assert isinstance(aggregate, dict)
    assert aggregate["scored_question_count"] == 2
    assert aggregate["infrastructure_failed_count"] == 0


def test_balanced_locomo_diagnostic_reports_natural_weighted_promotion(tmp_path: Path) -> None:
    run = tmp_path / "diagnostic"
    promotion = {
        "metric": "natural-category-weighted-accuracy-v1",
        "category_weights": {"1": 282, "2": 321, "3": 96, "4": 841},
        "minimum_accuracy": 0.82,
        "maximum_infrastructure_failures": 0,
        "maximum_retrieval_p95_ms": 4_000,
    }
    _write_json(
        run / "manifest.json",
        {
            "schema_version": 1,
            "suite": "locomo-200",
            "protocol": {"contract": {"diagnostic_promotion": promotion}},
            "question_set": {"question_count": 4},
        },
    )
    for category in range(1, 5):
        _write_json(
            run / "questions" / f"q{category}.json",
            {
                "question_id": f"q{category}",
                "category": category,
                "outcome": "wrong" if category == 3 else "correct",
                "retrieval_latency_ms": 1.0,
            },
        )

    aggregate = report_locomo(run)

    assert aggregate["natural_weighted_accuracy"] == pytest.approx(1444 / 1540)
    assert aggregate["diagnostic_promotion"] == {
        "metric": "natural-category-weighted-accuracy-v1",
        "minimum_accuracy": 0.82,
        "maximum_infrastructure_failures": 0,
        "maximum_retrieval_p95_ms": 4_000,
        "passed": True,
    }


def test_paid_plans_expose_inputs_outputs_and_spend_boundary() -> None:
    locomo = paid_plan("locomo-full", help_only=True)
    coding = paid_plan("coding-ab", help_only=True)
    assert "MAX_CALL_COST_USD" in locomo["required"]
    assert "MAX_CALL_COST_USD" not in coding["required"]
    assert locomo["inputs"]["protocol"] == "benchmarks/locomo/v01-protocol.json"
    assert len(locomo["inputs"]["protocol_sha256"]) == 64
    assert locomo["expected_output"] == "benchmark_results/locomo-full/<RUN_ID>"
    assert coding["expected_output"] == "benchmark_results/coding-ab/<RUN_ID>"


def test_locomo_provider_caps_completion_output(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"answer":"Friday"}'}}], "usage": {}}

    class Client:
        def post(self, path: str, *, json: dict[str, object]) -> Response:
            captured.update({"path": path, "json": json})
            return Response()

    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: Client())
    provider = TextProvider(
        "ANSWER",
        environment={
            "CODECAIRN_ANSWER_MODEL": "model",
            "CODECAIRN_ANSWER_BASE_URL": "https://provider.example/v1",
            "CODECAIRN_ANSWER_API_KEY": "secret",
        },
    )
    value, _usage = provider.complete(system="system", prompt="prompt")

    assert value == {"answer": "Friday"}
    assert captured["path"] == "chat/completions"
    request = cast(dict[str, object], captured["json"])
    assert request["max_completion_tokens"] == 512
    messages = cast(list[dict[str, str]], request["messages"])
    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[1] == {"role": "user", "content": "system\n\nprompt"}


def test_locomo_worker_count_is_explicit_and_bounded() -> None:
    assert _worker_count({}) == 1
    assert _worker_count({"CODECAIRN_EVAL_WORKERS": "4"}) == 4
    with pytest.raises(ValueError, match="between 1 and 8"):
        _worker_count({"CODECAIRN_EVAL_WORKERS": "9"})


def test_locomo_preflights_each_namespace_once_before_parallel_questions() -> None:
    calls: list[tuple[str, str]] = []

    class Runtime:
        def recall(self, query: str, *, repo_key: str, limit: int, token_budget: int) -> None:
            calls.append((query, repo_key))

    questions = (
        Question("q1", "sample-a", "first", "a", 1),
        Question("q2", "sample-a", "second", "b", 1),
        Question("q3", "sample-b", "third", "c", 1),
    )
    _preflight_recall(Runtime(), questions)

    assert calls == [("first", "locomo/sample-a"), ("third", "locomo/sample-b")]
