from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from codecairn.evaluation.artifacts import canonical_sha256, write_json_exclusive
from codecairn.evaluation.locomo import (
    LoCoMoConversation,
    LoCoMoDataset,
    LoCoMoQuestion,
    load_locomo_question_set,
)


def test_windowed_question_set_selects_a_bounded_rank_range(tmp_path: Path) -> None:
    questions = tuple(
        LoCoMoQuestion(
            question_id=f"q-{index}",
            question=f"Question {index}?",
            golden_answer=None,
            adversarial_answer=None,
            category=1,
            evidence=(),
        )
        for index in range(5)
    )
    dataset = LoCoMoDataset(
        source_path="synthetic",
        sha256="a" * 64,
        conversations=(
            LoCoMoConversation(
                sample_id="synthetic",
                speaker_a="A",
                speaker_b="B",
                sessions=(),
                questions=questions,
            ),
        ),
    )
    path = tmp_path / "windowed-question-set.json"
    write_json_exclusive(
        path,
        {
            "schema_version": 1,
            "selection_id": "windowed-question-set",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-window-v1",
            "seed": "window-seed",
            "category_targets": {"1": 3},
            "category_offsets": {"1": 1},
            "selection_sha256": (
                "15f89304f4795805e0436f99f42ebab0880d0379ae3ce2c31862a5e94bd42a07"
            ),
        },
    )

    loaded = load_locomo_question_set(path, dataset=dataset)

    assert loaded.question_ids == ("q-0", "q-1", "q-3")
    assert loaded.public_manifest["category_offsets"] == {"1": 1}


def test_windowed_question_set_fails_closed_on_invalid_windows(tmp_path: Path) -> None:
    questions = tuple(
        LoCoMoQuestion(
            question_id=f"q-{index}",
            question=f"Question {index}?",
            golden_answer=None,
            adversarial_answer=None,
            category=1,
            evidence=(),
        )
        for index in range(2)
    )
    dataset = LoCoMoDataset(
        source_path="synthetic",
        sha256="b" * 64,
        conversations=(
            LoCoMoConversation(
                sample_id="synthetic",
                speaker_a="A",
                speaker_b="B",
                sessions=(),
                questions=questions,
            ),
        ),
    )
    missing_offsets = tmp_path / "missing-offsets.json"
    write_json_exclusive(
        missing_offsets,
        {
            "schema_version": 1,
            "selection_id": "missing-offsets",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-window-v1",
            "seed": "window-seed",
            "category_targets": {"1": 1},
            "selection_sha256": "0" * 64,
        },
    )
    oversized_window = tmp_path / "oversized-window.json"
    write_json_exclusive(
        oversized_window,
        {
            "schema_version": 1,
            "selection_id": "oversized-window",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-window-v1",
            "seed": "window-seed",
            "category_targets": {"1": 2},
            "category_offsets": {"1": 1},
            "selection_sha256": "0" * 64,
        },
    )
    legacy_offsets = tmp_path / "legacy-offsets.json"
    write_json_exclusive(
        legacy_offsets,
        {
            "schema_version": 1,
            "selection_id": "legacy-offsets",
            "dataset_sha256": dataset.sha256,
            "algorithm": "stratified-sha256-v1",
            "seed": "window-seed",
            "category_targets": {"1": 1},
            "category_offsets": {"1": 0},
            "selection_sha256": "0" * 64,
        },
    )

    with pytest.raises(ValueError, match="Category offsets must be a JSON object"):
        load_locomo_question_set(missing_offsets, dataset=dataset)
    with pytest.raises(ValueError, match="category window exceeds"):
        load_locomo_question_set(oversized_window, dataset=dataset)
    with pytest.raises(ValueError, match="legacy question-set algorithm"):
        load_locomo_question_set(legacy_offsets, dataset=dataset)


def test_explicit_question_set_preserves_dataset_order_and_validates_category_counts(
    tmp_path: Path,
) -> None:
    questions = tuple(
        LoCoMoQuestion(
            question_id=f"q-{index}",
            question=f"Question {index}?",
            golden_answer=None,
            adversarial_answer=None,
            category=1 if index < 2 else 2,
            evidence=(),
        )
        for index in range(4)
    )
    dataset = LoCoMoDataset(
        source_path="synthetic",
        sha256="c" * 64,
        conversations=(
            LoCoMoConversation(
                sample_id="synthetic",
                speaker_a="A",
                speaker_b="B",
                sessions=(),
                questions=questions,
            ),
        ),
    )
    selected = ("q-3", "q-0")
    selection_sha256 = hashlib.sha256(
        json.dumps(sorted(selected), separators=(",", ":")).encode()
    ).hexdigest()
    path = tmp_path / "explicit-question-set.json"
    write_json_exclusive(
        path,
        {
            "schema_version": 1,
            "selection_id": "explicit-question-set",
            "dataset_sha256": dataset.sha256,
            "algorithm": "explicit-question-ids-v1",
            "seed": "base-run-infrastructure-failures",
            "category_targets": {"1": 1, "2": 1},
            "question_ids": list(selected),
            "selection_sha256": selection_sha256,
        },
    )

    loaded = load_locomo_question_set(path, dataset=dataset)

    assert loaded.question_ids == ("q-0", "q-3")
    assert loaded.public_manifest["algorithm"] == "explicit-question-ids-v1"

    forged = json.loads(path.read_text(encoding="utf-8"))
    forged["category_targets"] = {"1": 2}
    forged_path = tmp_path / "forged-explicit-question-set.json"
    write_json_exclusive(forged_path, forged)
    with pytest.raises(ValueError, match="category targets do not match"):
        load_locomo_question_set(forged_path, dataset=dataset)


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


def test_v15_protocol_assets_remain_immutable_historical_evidence() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    diagnostic_40_path = benchmark_root / "diagnostic-40-v15.json"
    diagnostic_200_path = benchmark_root / "diagnostic-200-v15.json"
    diagnostic_40 = json.loads(diagnostic_40_path.read_text())

    assert hashlib.sha256(diagnostic_40_path.read_bytes()).hexdigest() == (
        "fc21653c35707b9f3f85ca20f3d592481c45cea408e3e924a2f48caab171dad8"
    )
    assert hashlib.sha256(diagnostic_200_path.read_bytes()).hexdigest() == (
        "9b2cf00627fbfd9c98ecff367c96b114b1aaf18a50a920b7faf6c1edd0df147e"
    )
    assert diagnostic_40["protocol"]["query_sketcher"] == (
        "codecairn/deterministic-query-sketch-v2"
    )
    assert diagnostic_40["protocol"]["fact_selector"] == ("bounded-dialogue-aware-cross-encoder-v4")
    assert diagnostic_40["protocol"]["context_renderer"] == (
        "exact-source-prioritized-facts-first-v7"
    )
    assert "context_evidence_slot_policy" not in diagnostic_40["protocol"]


def test_v16_preflight_holdout_and_promotion_share_runtime_protocol(
    tmp_path: Path,
) -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    diagnostic_40_v15 = json.loads((benchmark_root / "diagnostic-40-v15.json").read_text())
    diagnostic_200_v15 = json.loads((benchmark_root / "diagnostic-200-v15.json").read_text())
    diagnostic_40 = json.loads((benchmark_root / "diagnostic-40-v16.json").read_text())
    diagnostic_160 = json.loads((benchmark_root / "diagnostic-160-holdout-v16.json").read_text())
    diagnostic_200 = json.loads((benchmark_root / "diagnostic-200-v16.json").read_text())

    assert (
        hashlib.sha256((benchmark_root / "diagnostic-40-v16.json").read_bytes()).hexdigest()
        == "85ea8afa0936519762f8ca57aa9edfde9aa7748644b3c638372c48d2e7756a99"
    )
    assert (
        hashlib.sha256(
            (benchmark_root / "diagnostic-160-holdout-v16.json").read_bytes()
        ).hexdigest()
        == "02a28013feb64ad034f736ebab1a86e665ebc05ccde0f0410a1dc14acef38e2c"
    )
    assert (
        hashlib.sha256((benchmark_root / "diagnostic-200-v16.json").read_bytes()).hexdigest()
        == "04517fed9274f85e03e46fc9c07b79ce61cd1e6ba9f61174a66ae99a83eae2f4"
    )
    assert diagnostic_40["category_targets"] == {str(category): 10 for category in range(1, 5)}
    assert diagnostic_160["category_targets"] == {str(category): 40 for category in range(1, 5)}
    assert diagnostic_160["category_offsets"] == {str(category): 10 for category in range(1, 5)}
    assert diagnostic_200["category_targets"] == {str(category): 50 for category in range(1, 5)}
    selection_keys = (
        "schema_version",
        "selection_id",
        "dataset_sha256",
        "algorithm",
        "seed",
        "category_targets",
        "selection_sha256",
    )
    assert {key: diagnostic_40[key] for key in selection_keys} == {
        key: diagnostic_40_v15[key] for key in selection_keys
    }
    assert {key: diagnostic_200[key] for key in selection_keys} == {
        key: diagnostic_200_v15[key] for key in selection_keys
    }
    assert diagnostic_40["variants"] == diagnostic_40_v15["variants"]
    assert diagnostic_40["gates"] == diagnostic_40_v15["gates"]
    assert diagnostic_40["protocol"] == diagnostic_160["protocol"]
    assert diagnostic_160["protocol"] == diagnostic_200["protocol"]
    expected_protocol = dict(diagnostic_40_v15["protocol"])
    expected_protocol.update(
        {
            "query_sketcher": "codecairn/deterministic-query-sketch-v3",
            "context_renderer": "exact-source-coverage-aware-facts-first-v8",
            "context_evidence_slot_policy": "typed-protected-child-support-v1",
            "context_semantic_support_fact_limit": 16,
            "context_quantity_transition_fact_limit": 12,
            "context_vocative_alias_fact_limit": 2,
            "context_prior_state_fact_limit": 4,
        }
    )
    assert diagnostic_40["protocol"] == expected_protocol
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
    assert diagnostic_40["protocol"]["query_sketcher"] == (
        "codecairn/deterministic-query-sketch-v3"
    )
    assert diagnostic_40["protocol"]["context_renderer"] == (
        "exact-source-coverage-aware-facts-first-v8"
    )
    assert diagnostic_40["protocol"]["context_evidence_slot_policy"] == (
        "typed-protected-child-support-v1"
    )
    assert diagnostic_40["protocol"]["context_semantic_support_fact_limit"] == 16
    assert diagnostic_40["protocol"]["context_quantity_transition_fact_limit"] == 12
    assert diagnostic_40["protocol"]["context_vocative_alias_fact_limit"] == 2
    assert diagnostic_40["protocol"]["context_prior_state_fact_limit"] == 4
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
        (diagnostic_160, "diagnostic-160.json"),
        (diagnostic_200, "diagnostic-200.json"),
    ):
        question_ids = _select_question_ids(
            synthetic_questions,
            seed=definition["seed"],
            targets=definition["category_targets"],
            offsets=definition.get("category_offsets"),
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
                **(
                    {}
                    if "category_offsets" not in definition
                    else {"category_offsets": definition["category_offsets"]}
                ),
                "selection_sha256": hashlib.sha256(
                    json.dumps(sorted(question_ids), separators=(",", ":")).encode()
                ).hexdigest(),
            },
        )
        loaded.append(load_locomo_question_set(path, dataset=dataset))
    preflight_ids = set(loaded[0].question_ids)
    holdout_ids = set(loaded[1].question_ids)
    diagnostic_ids = set(loaded[2].question_ids)
    assert len(preflight_ids) == 40
    assert len(holdout_ids) == 160
    assert not preflight_ids & holdout_ids
    assert preflight_ids | holdout_ids == diagnostic_ids
    assert holdout_ids == diagnostic_ids - preflight_ids

    assert diagnostic_40["gates"]["required_scored_questions_per_variant"] == 40
    assert "variants" not in diagnostic_200
    assert "gates" not in diagnostic_200
    promotion = diagnostic_200["promotion"]
    assert promotion["source_selection"] == {
        "selection_id": diagnostic_40["selection_id"],
        "question_set_sha256": hashlib.sha256(
            (benchmark_root / "diagnostic-40-v16.json").read_bytes()
        ).hexdigest(),
        "selection_sha256": diagnostic_40["selection_sha256"],
        "protocol_sha256": _canonical_sha256(diagnostic_40["protocol"]),
        "gates_sha256": _canonical_sha256(diagnostic_40["gates"]),
    }
    assert promotion["required_scored_questions"] == 200
    assert promotion["frozen_baseline"] == diagnostic_200_v15["promotion"]["frozen_baseline"]
    assert promotion["gates"] == diagnostic_200_v15["promotion"]["gates"]
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


def test_v17_changes_only_the_quantity_slot_policy_contract() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    paths = {
        "40": benchmark_root / "diagnostic-40-v17.json",
        "160": benchmark_root / "diagnostic-160-holdout-v17.json",
        "200": benchmark_root / "diagnostic-200-v17.json",
    }
    expected_hashes = {
        "40": "03b7a000d8f263048a118b92d5cc008e6f3b25214acfccc45b07c80e34f1df3b",
        "160": "26ae021c9964ffb7df336eaf3ea730aaf05a330fd58f8addf25a5c08eab42e1f",
        "200": "d7b63a9e05e2619223943ef9d36b18f7dfe3d7d6365a4cb0bbcac385a13109dd",
    }
    v17 = {name: json.loads(path.read_text()) for name, path in paths.items()}
    v16 = {
        "40": json.loads((benchmark_root / "diagnostic-40-v16.json").read_text()),
        "160": json.loads((benchmark_root / "diagnostic-160-holdout-v16.json").read_text()),
        "200": json.loads((benchmark_root / "diagnostic-200-v16.json").read_text()),
    }

    for name, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hashes[name]
        expected_protocol = dict(v16[name]["protocol"])
        expected_protocol["context_evidence_slot_policy"] = "typed-protected-child-support-v2"
        assert v17[name]["protocol"] == expected_protocol
    assert v17["40"]["protocol"] == v17["160"]["protocol"] == v17["200"]["protocol"]
    assert {key: value for key, value in v17["40"].items() if key != "protocol"} == {
        key: value for key, value in v16["40"].items() if key != "protocol"
    }
    assert {key: value for key, value in v17["160"].items() if key != "protocol"} == {
        key: value for key, value in v16["160"].items() if key != "protocol"
    }
    assert {
        key: value for key, value in v17["200"].items() if key not in {"protocol", "promotion"}
    } == {key: value for key, value in v16["200"].items() if key not in {"protocol", "promotion"}}

    source = v17["200"]["promotion"]["source_selection"]
    assert source["question_set_sha256"] == expected_hashes["40"]
    assert source["protocol_sha256"] == _canonical_sha256(v17["40"]["protocol"])
    assert source["gates_sha256"] == _canonical_sha256(v17["40"]["gates"])
    assert {
        key: value for key, value in v17["200"]["promotion"].items() if key != "source_selection"
    } == {key: value for key, value in v16["200"]["promotion"].items() if key != "source_selection"}


def test_v18_freezes_lossless_child_recall_and_calendar_query_contracts() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    paths = {
        "40": benchmark_root / "diagnostic-40-v18.json",
        "160": benchmark_root / "diagnostic-160-holdout-v18.json",
        "200": benchmark_root / "diagnostic-200-v18.json",
    }
    expected_hashes = {
        "40": "52c44fb577c641c7ca9d74a684dd8559bc21d1042051a60dd4d8f8bf7b608d00",
        "160": "123234add282435631101a2a106bf719ac0709f51d1f0f377086d61c48cee8a3",
        "200": "cb83752f66bc1b570267f2533e980a0a7df36cdc758f68fcb4c704cbaf0f5a8a",
    }
    v18 = {name: json.loads(path.read_text()) for name, path in paths.items()}
    v17 = {
        "40": json.loads((benchmark_root / "diagnostic-40-v17.json").read_text()),
        "160": json.loads((benchmark_root / "diagnostic-160-holdout-v17.json").read_text()),
        "200": json.loads((benchmark_root / "diagnostic-200-v17.json").read_text()),
    }

    for name, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hashes[name]
        expected_protocol = dict(v17[name]["protocol"])
        expected_protocol.update(
            {
                "query_sketcher": "codecairn/deterministic-query-sketch-v4",
                "temporal_lane": "explicit-calendar-prefix-v2",
                "fact_selector": "bounded-dialogue-aware-cross-encoder-v5",
                "paid_scoring_gate": "dual-retrieval-context-coverage-v1",
            }
        )
        assert v18[name]["protocol"] == expected_protocol
    assert v18["40"]["protocol"] == v18["160"]["protocol"] == v18["200"]["protocol"]
    assert {key: value for key, value in v18["40"].items() if key != "protocol"} == {
        key: value for key, value in v17["40"].items() if key != "protocol"
    }
    assert {key: value for key, value in v18["160"].items() if key != "protocol"} == {
        key: value for key, value in v17["160"].items() if key != "protocol"
    }
    assert {
        key: value for key, value in v18["200"].items() if key not in {"protocol", "promotion"}
    } == {key: value for key, value in v17["200"].items() if key not in {"protocol", "promotion"}}

    source = v18["200"]["promotion"]["source_selection"]
    assert source["question_set_sha256"] == expected_hashes["40"]
    assert source["protocol_sha256"] == canonical_sha256(v18["40"]["protocol"])
    assert source["gates_sha256"] == _canonical_sha256(v18["40"]["gates"])
    holdout = v18["200"]["promotion"]["holdout_selection"]
    assert holdout == {
        "selection_id": v18["160"]["selection_id"],
        "question_set_sha256": expected_hashes["160"],
        "selection_sha256": v18["160"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v18["160"]["protocol"]),
    }
    assert {
        key: value
        for key, value in v18["200"]["promotion"].items()
        if key not in {"source_selection", "holdout_selection"}
    } == {key: value for key, value in v17["200"]["promotion"].items() if key != "source_selection"}


def test_v19_freezes_compact_flat_context_and_high_confidence_parent_slot() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    paths = {
        "40": benchmark_root / "diagnostic-40-v19.json",
        "160": benchmark_root / "diagnostic-160-holdout-v19.json",
        "200": benchmark_root / "diagnostic-200-v19.json",
    }
    expected_hashes = {
        "40": "e17ee04bb9ed5ac1f6b5a7072672820ef6195c957a94961a7a1dcbe8cece078f",
        "160": "68efa6ec4a3de41a1a9a8a6e705fffff69bb1147fa4263a55a0c6c9640231dad",
        "200": "8466ceca0df5821f149e6ac110b1a9fb2c14723cb198f75aa063a4d174b92461",
    }
    v19 = {name: json.loads(path.read_text()) for name, path in paths.items()}
    v18 = {
        "40": json.loads((benchmark_root / "diagnostic-40-v18.json").read_text()),
        "160": json.loads((benchmark_root / "diagnostic-160-holdout-v18.json").read_text()),
        "200": json.loads((benchmark_root / "diagnostic-200-v18.json").read_text()),
    }

    for name, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hashes[name]
        expected_protocol = dict(v18[name]["protocol"])
        expected_protocol.update(
            {
                "query_sketcher": "codecairn/deterministic-query-sketch-v5",
                "context_renderer": "exact-source-flat-facts-first-v9",
                "context_evidence_slot_policy": "typed-protected-child-support-v3",
                "context_high_confidence_parent_fact_limit": 4,
                "context_high_confidence_parent_score_threshold": 5.5,
            }
        )
        assert v19[name]["protocol"] == expected_protocol
    assert v19["40"]["protocol"] == v19["160"]["protocol"] == v19["200"]["protocol"]
    assert {key: value for key, value in v19["40"].items() if key != "protocol"} == {
        key: value for key, value in v18["40"].items() if key != "protocol"
    }
    assert {key: value for key, value in v19["160"].items() if key != "protocol"} == {
        key: value for key, value in v18["160"].items() if key != "protocol"
    }
    assert {
        key: value for key, value in v19["200"].items() if key not in {"protocol", "promotion"}
    } == {key: value for key, value in v18["200"].items() if key not in {"protocol", "promotion"}}

    source = v19["200"]["promotion"]["source_selection"]
    assert source == {
        "selection_id": v19["40"]["selection_id"],
        "question_set_sha256": expected_hashes["40"],
        "selection_sha256": v19["40"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v19["40"]["protocol"]),
        "gates_sha256": _canonical_sha256(v19["40"]["gates"]),
    }
    holdout = v19["200"]["promotion"]["holdout_selection"]
    assert holdout == {
        "selection_id": v19["160"]["selection_id"],
        "question_set_sha256": expected_hashes["160"],
        "selection_sha256": v19["160"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v19["160"]["protocol"]),
    }
    assert {
        key: value
        for key, value in v19["200"]["promotion"].items()
        if key not in {"source_selection", "holdout_selection"}
    } == {
        key: value
        for key, value in v18["200"]["promotion"].items()
        if key not in {"source_selection", "holdout_selection"}
    }


def test_v20_changes_only_the_ablation_core_non_regression_gate() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    paths = {
        "40": benchmark_root / "diagnostic-40-v20.json",
        "160": benchmark_root / "diagnostic-160-holdout-v20.json",
        "200": benchmark_root / "diagnostic-200-v20.json",
    }
    expected_hashes = {
        "40": "b186388803a029e641860da4848fcd274f72d52de151b2581c28881426fa7520",
        "160": "68efa6ec4a3de41a1a9a8a6e705fffff69bb1147fa4263a55a0c6c9640231dad",
        "200": "dfcc841e7465ca3a993c1dd32c9d8307767b058ee7c73c9fbb4baafb6dd968cb",
    }
    v20 = {name: json.loads(path.read_text()) for name, path in paths.items()}
    v19 = {
        "40": json.loads((benchmark_root / "diagnostic-40-v19.json").read_text()),
        "160": json.loads((benchmark_root / "diagnostic-160-holdout-v19.json").read_text()),
        "200": json.loads((benchmark_root / "diagnostic-200-v19.json").read_text()),
    }

    for name, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hashes[name]
        assert v20[name]["protocol"] == v19[name]["protocol"]
    expected_gates = dict(v19["40"]["gates"])
    expected_gates["hierarchy_no_neighbors_vs_episode_minimum_accuracy_delta_points"] = 0
    assert v20["40"]["gates"] == expected_gates
    assert v20["160"] == v19["160"]
    assert {key: value for key, value in v20["200"].items() if key != "promotion"} == {
        key: value for key, value in v19["200"].items() if key != "promotion"
    }

    source = v20["200"]["promotion"]["source_selection"]
    assert source == {
        "selection_id": v20["40"]["selection_id"],
        "question_set_sha256": expected_hashes["40"],
        "selection_sha256": v20["40"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v20["40"]["protocol"]),
        "gates_sha256": _canonical_sha256(v20["40"]["gates"]),
    }
    holdout = v20["200"]["promotion"]["holdout_selection"]
    assert holdout == {
        "selection_id": v20["160"]["selection_id"],
        "question_set_sha256": expected_hashes["160"],
        "selection_sha256": v20["160"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v20["160"]["protocol"]),
    }
    assert {
        key: value
        for key, value in v20["200"]["promotion"].items()
        if key not in {"source_selection", "holdout_selection"}
    } == {
        key: value
        for key, value in v19["200"]["promotion"].items()
        if key not in {"source_selection", "holdout_selection"}
    }


def test_v21_bounds_fact_reranking_without_changing_other_contracts() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    paths = {
        "40": benchmark_root / "diagnostic-40-v21.json",
        "160": benchmark_root / "diagnostic-160-holdout-v21.json",
        "200": benchmark_root / "diagnostic-200-v21.json",
    }
    expected_hashes = {
        "40": "79f464237db1f9e2f5f04c27cd5c05139e0c31af9bc3bb9d6e7dd8662e401f7b",
        "160": "8eab0e5330307f311766c53b1078c4e7e86417ec4a2abca01b68844d173eaaf9",
        "200": "82c30dc1a247abcb26f5f65abcefb9fa4798fcfaae11a0c1352d5834a49d2258",
    }
    v21 = {name: json.loads(path.read_text()) for name, path in paths.items()}
    v20 = {
        "40": json.loads((benchmark_root / "diagnostic-40-v20.json").read_text()),
        "160": json.loads((benchmark_root / "diagnostic-160-holdout-v20.json").read_text()),
        "200": json.loads((benchmark_root / "diagnostic-200-v20.json").read_text()),
    }

    for name, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hashes[name]
        expected_protocol = dict(v20[name]["protocol"])
        expected_protocol.update(
            {
                "fact_rerank_max_candidates": 192,
                "fact_rerank_max_candidates_per_parent": 20,
                "fact_rerank_max_document_chars": 1024,
            }
        )
        assert v21[name]["protocol"] == expected_protocol
    assert v21["40"]["gates"] == v20["40"]["gates"]
    assert {
        key: value for key, value in v21["200"].items() if key not in {"promotion", "protocol"}
    } == {key: value for key, value in v20["200"].items() if key not in {"promotion", "protocol"}}

    source = v21["200"]["promotion"]["source_selection"]
    assert source == {
        "selection_id": v21["40"]["selection_id"],
        "question_set_sha256": expected_hashes["40"],
        "selection_sha256": v21["40"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v21["40"]["protocol"]),
        "gates_sha256": _canonical_sha256(v21["40"]["gates"]),
    }
    holdout = v21["200"]["promotion"]["holdout_selection"]
    assert holdout == {
        "selection_id": v21["160"]["selection_id"],
        "question_set_sha256": expected_hashes["160"],
        "selection_sha256": v21["160"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v21["160"]["protocol"]),
    }
    assert {
        key: value
        for key, value in v21["200"]["promotion"].items()
        if key not in {"source_selection", "holdout_selection"}
    } == {
        key: value
        for key, value in v20["200"]["promotion"].items()
        if key not in {"source_selection", "holdout_selection"}
    }


def test_v22_changes_only_the_local_retrieval_latency_slo() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    paths = {
        "40": benchmark_root / "diagnostic-40-v22.json",
        "160": benchmark_root / "diagnostic-160-holdout-v22.json",
        "200": benchmark_root / "diagnostic-200-v22.json",
    }
    expected_hashes = {
        "40": "0781479dd57df13210c5f49f4e70fda6770994d674b2ada680324002502d796b",
        "160": "8eab0e5330307f311766c53b1078c4e7e86417ec4a2abca01b68844d173eaaf9",
        "200": "451f36d7f7630043e3df0ae339f77e61620a10053101c35f9d312756e07abfb0",
    }
    v22 = {name: json.loads(path.read_text()) for name, path in paths.items()}
    v21 = {
        "40": json.loads((benchmark_root / "diagnostic-40-v21.json").read_text()),
        "160": json.loads((benchmark_root / "diagnostic-160-holdout-v21.json").read_text()),
        "200": json.loads((benchmark_root / "diagnostic-200-v21.json").read_text()),
    }

    for name, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hashes[name]
        assert v22[name]["protocol"] == v21[name]["protocol"]
    expected_selection_gates = dict(v21["40"]["gates"])
    expected_selection_gates["selected_maximum_retrieval_p95_ms"] = 4000
    assert v22["40"]["gates"] == expected_selection_gates
    assert v22["160"] == v21["160"]

    source = v22["200"]["promotion"]["source_selection"]
    assert source == {
        "selection_id": v22["40"]["selection_id"],
        "question_set_sha256": expected_hashes["40"],
        "selection_sha256": v22["40"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v22["40"]["protocol"]),
        "gates_sha256": _canonical_sha256(v22["40"]["gates"]),
    }
    holdout = v22["200"]["promotion"]["holdout_selection"]
    assert holdout == {
        "selection_id": v22["160"]["selection_id"],
        "question_set_sha256": expected_hashes["160"],
        "selection_sha256": v22["160"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v22["160"]["protocol"]),
    }
    expected_promotion = json.loads(json.dumps(v21["200"]["promotion"]))
    expected_promotion["source_selection"] = source
    expected_promotion["gates"]["maximum_retrieval_p95_ms"] = 4000
    assert v22["200"]["promotion"] == expected_promotion


def test_v23_changes_only_the_grounded_answer_contracts() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    paths = {
        "40": benchmark_root / "diagnostic-40-v23.json",
        "160": benchmark_root / "diagnostic-160-holdout-v23.json",
        "200": benchmark_root / "diagnostic-200-v23.json",
    }
    expected_hashes = {
        "40": "e5f2d077c9d0029dd5a67c314a84652abf3503fc224f15ba2e32e399f3de0c00",
        "160": "05c8d03f7be2667ccca5d5240345c45bcba3d5843e17037bb17fc27c0cbe0198",
        "200": "87c346a1a06257819c21a57e695a27dfae5e5c0c34820ddae860df2333cfede9",
    }
    v23 = {name: json.loads(path.read_text()) for name, path in paths.items()}
    v22 = {
        "40": json.loads((benchmark_root / "diagnostic-40-v22.json").read_text()),
        "160": json.loads((benchmark_root / "diagnostic-160-holdout-v22.json").read_text()),
        "200": json.loads((benchmark_root / "diagnostic-200-v22.json").read_text()),
    }

    for name, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hashes[name]
        expected_protocol = dict(v22[name]["protocol"])
        expected_protocol.update(
            {
                "answer_evidence_contract": "grounded-cited-answer-v14",
                "answer_retry_contract": "grounded-answer-contract-retry-v2",
            }
        )
        assert v23[name]["protocol"] == expected_protocol
    assert v23["40"]["gates"] == v22["40"]["gates"]
    assert {key: value for key, value in v23["160"].items() if key != "protocol"} == {
        key: value for key, value in v22["160"].items() if key != "protocol"
    }

    source = v23["200"]["promotion"]["source_selection"]
    assert source == {
        "selection_id": v23["40"]["selection_id"],
        "question_set_sha256": expected_hashes["40"],
        "selection_sha256": v23["40"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v23["40"]["protocol"]),
        "gates_sha256": _canonical_sha256(v23["40"]["gates"]),
    }
    holdout = v23["200"]["promotion"]["holdout_selection"]
    assert holdout == {
        "selection_id": v23["160"]["selection_id"],
        "question_set_sha256": expected_hashes["160"],
        "selection_sha256": v23["160"]["selection_sha256"],
        "protocol_sha256": canonical_sha256(v23["160"]["protocol"]),
    }
    expected_promotion = json.loads(json.dumps(v22["200"]["promotion"]))
    expected_promotion["source_selection"] = source
    expected_promotion["holdout_selection"] = holdout
    assert v23["200"]["promotion"] == expected_promotion


def test_v23_full_question_set_freezes_all_standard_locomo_questions() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    full_path = benchmark_root / "full-1540-v23.json"
    full = json.loads(full_path.read_text())
    diagnostic = json.loads((benchmark_root / "diagnostic-200-v23.json").read_text())

    assert full["selection_id"] == "locomo-full-1540-v23"
    assert full["category_targets"] == {
        "1": 282,
        "2": 321,
        "3": 96,
        "4": 841,
    }
    assert sum(full["category_targets"].values()) == 1540
    assert full["selection_sha256"] == (
        "caf55b45f266fe5738025333400331c92a660a5a607d107a2141e10f729e4b37"
    )
    expected_protocol = dict(diagnostic["protocol"])
    expected_protocol.pop("paid_scoring_gate")
    assert full["protocol"] == expected_protocol


def test_v23_failed_only_repair_set_is_bound_to_the_negative_full_run() -> None:
    benchmark_root = Path(__file__).parents[1] / "benchmarks" / "locomo"
    repair_path = benchmark_root / "repair-717-v23-d19793c.json"
    repair = json.loads(repair_path.read_text())
    full = json.loads((benchmark_root / "full-1540-v23.json").read_text())

    assert hashlib.sha256(repair_path.read_bytes()).hexdigest() == (
        "e8f476892ccd6b99c125938a964a2e23eb6f1b455e74861f949560e50e6903d5"
    )
    assert repair["algorithm"] == "explicit-question-ids-v1"
    assert repair["category_targets"] == {"1": 117, "2": 131, "3": 50, "4": 419}
    assert len(repair["question_ids"]) == 717
    assert repair["selection_sha256"] == (
        "6c9955ca66a654bd0f5c9e1b2c5342bae839d78f61469256f517c40a15769a58"
    )
    assert len(set(repair["question_ids"])) == 717
    selected_ids_sha256 = hashlib.sha256(
        json.dumps(
            sorted(repair["question_ids"]),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert selected_ids_sha256 == repair["selection_sha256"]
    assert repair["protocol"] == full["protocol"]
    source = repair["repair_source"]
    assert source["contract"] == "failed-question-exact-replacement-v1"
    assert source["failed_question_count"] == 717
    assert source["failed_question_ids_sha256"] == selected_ids_sha256
    assert (
        source["target_question_set_sha256"]
        == hashlib.sha256((benchmark_root / "full-1540-v23.json").read_bytes()).hexdigest()
    )
    assert source["target_protocol_sha256"] == canonical_sha256(full["protocol"])


def _select_question_ids(
    questions: tuple[LoCoMoQuestion, ...],
    *,
    seed: str,
    targets: dict[str, int],
    offsets: dict[str, int] | None = None,
) -> tuple[str, ...]:
    selected: set[str] = set()
    for raw_category, target in sorted(targets.items()):
        category = int(raw_category)
        offset = 0 if offsets is None else offsets[raw_category]
        candidates = [question for question in questions if question.category == category]
        candidates.sort(
            key=lambda question: (
                hashlib.sha256(f"{seed}\0{question.question_id}".encode()).hexdigest(),
                question.question_id,
            )
        )
        selected.update(question.question_id for question in candidates[offset : offset + target])
    return tuple(question.question_id for question in questions if question.question_id in selected)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
