from __future__ import annotations

import hashlib
import json
from pathlib import Path

from codecairn.evaluation.artifacts import write_json_exclusive
from codecairn.evaluation.locomo import (
    LoCoMoConversation,
    LoCoMoDataset,
    LoCoMoQuestion,
    load_locomo_question_set,
)


def test_v14_protocol_assets_remain_immutable_historical_evidence() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    diagnostic_40_path = benchmark_root / "diagnostic-40-v14.json"
    diagnostic_200_path = benchmark_root / "diagnostic-200-v14.json"
    diagnostic_40 = json.loads(diagnostic_40_path.read_text())

    assert hashlib.sha256(diagnostic_40_path.read_bytes()).hexdigest() == (
        "8d6a83ff0a4c2be777edd9d945e11dd4357435e7240e44337a76ea21b0706efb"
    )
    assert hashlib.sha256(diagnostic_200_path.read_bytes()).hexdigest() == (
        "12d9b83bcc0f480c9d88eac452b62e2b44c8ad8d79bb9971ea82347f61095b31"
    )
    assert diagnostic_40["protocol"]["fact_selector"] == ("bounded-authoritative-cross-encoder-v1")
    assert diagnostic_40["protocol"]["context_renderer"] == "scored-facts-first-v5"


def test_40_question_ablation_and_200_question_promotion_share_runtime_protocol(
    tmp_path: Path,
) -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    diagnostic_40 = json.loads((benchmark_root / "diagnostic-40-v15.json").read_text())
    diagnostic_200 = json.loads((benchmark_root / "diagnostic-200-v15.json").read_text())

    assert diagnostic_40["category_targets"] == {str(category): 10 for category in range(1, 5)}
    assert diagnostic_200["category_targets"] == {str(category): 50 for category in range(1, 5)}
    assert diagnostic_40["protocol"] == diagnostic_200["protocol"]
    assert diagnostic_40["protocol"]["answer_retry_contract"] == (
        "grounded-answer-contract-retry-v1"
    )
    assert diagnostic_40["protocol"]["model_attempt_journal_contract"] == (
        "locomo-model-attempt-journal-v1"
    )
    assert diagnostic_40["protocol"]["checkpoint_policy"] == (
        "journal-replay-or-unknown-spend-fail-closed-v3"
    )
    assert diagnostic_40["protocol"]["answer_response_max_attempts"] == 2
    assert diagnostic_40["protocol"]["judge_response_max_attempts"] == 3
    assert diagnostic_40["protocol"]["judge_response_max_chars"] == 32_768
    assert diagnostic_40["protocol"]["seed"] == 17
    assert diagnostic_40["protocol"]["enrichment_order"] == (
        "matched-neighbor-then-capacity-aware-dialogue-rerank-v7"
    )
    assert diagnostic_40["protocol"]["fact_selector"] == ("bounded-dialogue-aware-cross-encoder-v4")
    assert diagnostic_40["protocol"]["fact_rerank_max_candidates"] == 256
    assert diagnostic_40["protocol"]["fact_rerank_max_candidates_per_parent"] == 24
    assert diagnostic_40["protocol"]["fact_rerank_max_selected_per_parent"] == 12
    assert diagnostic_40["protocol"]["fact_rerank_max_document_chars"] == 2_048
    assert diagnostic_40["protocol"]["worker_reranker_warmup"] == (
        "one-local-document-before-question-timing-v1"
    )
    assert diagnostic_40["protocol"]["context_renderer"] == (
        "exact-source-prioritized-facts-first-v7"
    )
    assert diagnostic_40["protocol"]["context_direct_match_prior"] == 2.0
    assert diagnostic_40["algorithm"] == diagnostic_200["algorithm"]
    assert diagnostic_40["seed"] == diagnostic_200["seed"]

    synthetic_questions = tuple(
        LoCoMoQuestion(
            question_id=f"question-{category}-{index}",
            question=f"Question {category}-{index}?",
            golden_answer=None,
            adversarial_answer=None,
            category=category,
            evidence=(),
        )
        for category in range(1, 5)
        for index in range(60)
    )
    dataset = LoCoMoDataset(
        source_path="synthetic",
        sha256=diagnostic_40["dataset_sha256"],
        conversations=(
            LoCoMoConversation(
                sample_id="synthetic",
                speaker_a="A",
                speaker_b="B",
                sessions=(),
                questions=synthetic_questions,
            ),
        ),
    )
    loaded = []
    for definition, name in (
        (diagnostic_40, "diagnostic-40.json"),
        (diagnostic_200, "diagnostic-200.json"),
    ):
        question_ids = _select_question_ids(
            synthetic_questions,
            seed=definition["seed"],
            targets=definition["category_targets"],
        )
        path = tmp_path / name
        write_json_exclusive(
            path,
            {
                "schema_version": 1,
                "selection_id": definition["selection_id"],
                "dataset_sha256": definition["dataset_sha256"],
                "algorithm": definition["algorithm"],
                "seed": definition["seed"],
                "category_targets": definition["category_targets"],
                "selection_sha256": hashlib.sha256(
                    json.dumps(sorted(question_ids), separators=(",", ":")).encode()
                ).hexdigest(),
            },
        )
        loaded.append(load_locomo_question_set(path, dataset=dataset))
    assert set(loaded[0].question_ids) <= set(loaded[1].question_ids)

    assert diagnostic_40["gates"]["required_scored_questions_per_variant"] == 40
    assert "variants" not in diagnostic_200
    assert "gates" not in diagnostic_200
    promotion = diagnostic_200["promotion"]
    assert promotion["source_selection"] == {
        "selection_id": diagnostic_40["selection_id"],
        "question_set_sha256": hashlib.sha256(
            (benchmark_root / "diagnostic-40-v15.json").read_bytes()
        ).hexdigest(),
        "selection_sha256": diagnostic_40["selection_sha256"],
        "protocol_sha256": _canonical_sha256(diagnostic_40["protocol"]),
        "gates_sha256": _canonical_sha256(diagnostic_40["gates"]),
    }
    assert promotion["required_scored_questions"] == 200
    assert promotion["frozen_baseline"] == {
        "run_id": "locomo-v5-diagnostic200-hierarchy-d5fb39c",
        "repository_commit": "d5fb39c31355b66b46a5600d1f4a7116d723dece",
        "summary_sha256": "539814e251d6b34492e21d1a497a836ca78129313faf9fac200abd7f3597456a",
        "selection_sha256": diagnostic_200["selection_sha256"],
        "scored_question_count": 200,
        "infrastructure_failed_count": 0,
        "single_hop_accuracy": 0.92,
    }
    assert promotion["gates"] == {
        "minimum_overall_accuracy": 0.78,
        "minimum_multi_hop_accuracy": 0.70,
        "minimum_open_domain_accuracy": 0.68,
        "maximum_single_hop_regression_points": 2.0,
        "maximum_infrastructure_failures": 0,
        "maximum_retrieval_p95_ms": 2500.0,
        "maximum_process_rss_bytes_exclusive": 2 * 1024 * 1024 * 1024,
    }


def _select_question_ids(
    questions: tuple[LoCoMoQuestion, ...],
    *,
    seed: str,
    targets: dict[str, int],
) -> tuple[str, ...]:
    selected: set[str] = set()
    for raw_category, target in sorted(targets.items()):
        category = int(raw_category)
        candidates = [question for question in questions if question.category == category]
        candidates.sort(
            key=lambda question: (
                hashlib.sha256(f"{seed}\0{question.question_id}".encode()).hexdigest(),
                question.question_id,
            )
        )
        selected.update(question.question_id for question in candidates[:target])
    return tuple(question.question_id for question in questions if question.question_id in selected)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
