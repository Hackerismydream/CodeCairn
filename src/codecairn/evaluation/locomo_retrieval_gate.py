from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from codecairn.evaluation.artifacts import canonical_sha256, file_sha256, read_json
from codecairn.evaluation.locomo import (
    LOCOMO_PAID_SCORING_GATE_CONTRACT,
    load_locomo_dataset,
    load_locomo_question_set,
    report_locomo,
    validate_locomo_retrieval_manifest_protocol,
)
from codecairn.evaluation.locomo_evidence import (
    LoCoMoEvidenceCoverageConfig,
    report_locomo_evidence_coverage,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "contract",
        "repository_commit",
        "dataset_sha256",
        "target_question_set_sha256",
        "target_selection_sha256",
        "target_question_count",
        "scored_question_set_sha256",
        "scored_selection_sha256",
        "scored_question_count",
        "protocol_sha256",
        "corpus_content_sha256",
        "query_vectors_content_sha256",
        "minimum_context_all_coverage",
        "maximum_context_tokens",
        "maximum_retrieval_p95_ms",
        "maximum_process_rss_bytes_exclusive",
        "sources",
        "receipt_sha256",
    }
)
_RECEIPT_SOURCE_FIELDS = frozenset(
    {
        "run_id",
        "selection_id",
        "question_set_sha256",
        "selection_sha256",
        "question_count",
        "context_all_coverage",
        "maximum_context_tokens",
        "retrieval_p95_ms",
        "max_process_rss_bytes",
        "manifest_sha256",
        "summary_sha256",
        "evidence_report_sha256",
        "resource_usage_sha256",
    }
)


class LoCoMoRetrievalGateReporter(Protocol):
    def report(self, run_dir: Path) -> dict[str, object]: ...

    def evidence(
        self,
        run_dir: Path,
        *,
        dataset_path: Path,
    ) -> dict[str, object]: ...


class _VerifiedArtifactReporter:
    def report(self, run_dir: Path) -> dict[str, object]:
        return report_locomo(run_dir)

    def evidence(
        self,
        run_dir: Path,
        *,
        dataset_path: Path,
    ) -> dict[str, object]:
        return report_locomo_evidence_coverage(
            LoCoMoEvidenceCoverageConfig(
                run_dir=run_dir,
                dataset_path=dataset_path,
            )
        )


@dataclass(frozen=True, slots=True)
class LoCoMoRetrievalGateConfig:
    target_question_set_path: Path
    scored_question_set_path: Path
    dataset_path: Path
    canary_run_dir: Path
    holdout_run_dir: Path
    repository_commit: str
    corpus_path: Path
    query_vectors_path: Path
    expected_canary_questions: int = 40
    expected_holdout_questions: int = 160
    minimum_context_all_coverage: float = 0.70
    maximum_context_tokens: int = 4_000
    maximum_retrieval_p95_ms: float = 2_500.0
    maximum_process_rss_bytes_exclusive: int = 2 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.repository_commit:
            raise ValueError("LoCoMo retrieval gate requires a repository commit")
        if self.expected_canary_questions < 1 or self.expected_holdout_questions < 1:
            raise ValueError("LoCoMo retrieval gate question counts must be positive")
        if not 0 < self.minimum_context_all_coverage <= 1:
            raise ValueError("LoCoMo retrieval gate coverage must be within (0, 1]")
        if self.maximum_context_tokens < 1:
            raise ValueError("LoCoMo retrieval gate context limit must be positive")
        if self.maximum_retrieval_p95_ms <= 0:
            raise ValueError("LoCoMo retrieval gate latency limit must be positive")
        if self.maximum_process_rss_bytes_exclusive < 1:
            raise ValueError("LoCoMo retrieval gate RSS limit must be positive")


def verify_locomo_retrieval_gate(
    config: LoCoMoRetrievalGateConfig,
    *,
    reporter: LoCoMoRetrievalGateReporter | None = None,
) -> dict[str, object]:
    """Verify disjoint retrieval evidence before constructing paid providers."""

    active_reporter = reporter or _VerifiedArtifactReporter()
    dataset = load_locomo_dataset(config.dataset_path)
    target_definition = _mapping(
        read_json(config.target_question_set_path),
        field="LoCoMo target question-set definition",
    )
    target = load_locomo_question_set(
        config.target_question_set_path,
        dataset=dataset,
    )
    scored = load_locomo_question_set(
        config.scored_question_set_path,
        dataset=dataset,
    )
    protocol = target.protocol
    if protocol is None or protocol.get("paid_scoring_gate") != LOCOMO_PAID_SCORING_GATE_CONTRACT:
        raise ValueError("LoCoMo target protocol has no supported paid-scoring gate")
    if scored.protocol != protocol:
        raise ValueError("LoCoMo scored question set changes the retrieval-gate protocol")
    protocol_sha256 = _canonical_sha256(protocol)
    promotion = _mapping(
        target_definition.get("promotion"),
        field="LoCoMo target promotion",
    )
    frozen_canary = _mapping(
        promotion.get("source_selection"),
        field="LoCoMo frozen canary selection",
    )
    frozen_holdout = _mapping(
        promotion.get("holdout_selection"),
        field="LoCoMo frozen holdout selection",
    )
    if (
        frozen_canary.get("protocol_sha256") != protocol_sha256
        or frozen_holdout.get("protocol_sha256") != protocol_sha256
    ):
        raise ValueError("LoCoMo frozen retrieval selections change the target protocol")

    corpus_manifest = _mapping(
        read_json(config.corpus_path / "manifest.json"),
        field="LoCoMo corpus manifest",
    )
    query_manifest = _mapping(
        read_json(config.query_vectors_path / "manifest.json"),
        field="LoCoMo query-vector manifest",
    )
    corpus_content_sha256 = _string(corpus_manifest, "content_sha256")
    query_content_sha256 = _string(query_manifest, "content_sha256")
    _mapping(
        corpus_manifest.get("build_contract"),
        field="LoCoMo corpus build contract",
    )

    sources = tuple(
        _verify_source_run(
            run_dir,
            dataset_path=config.dataset_path,
            expected_question_count=expected_count,
            repository_commit=config.repository_commit,
            protocol=protocol,
            protocol_sha256=protocol_sha256,
            corpus_content_sha256=corpus_content_sha256,
            query_content_sha256=query_content_sha256,
            minimum_context_all_coverage=config.minimum_context_all_coverage,
            maximum_context_tokens=config.maximum_context_tokens,
            maximum_retrieval_p95_ms=config.maximum_retrieval_p95_ms,
            maximum_process_rss_bytes_exclusive=(config.maximum_process_rss_bytes_exclusive),
            reporter=active_reporter,
        )
        for run_dir, expected_count in (
            (config.canary_run_dir, config.expected_canary_questions),
            (config.holdout_run_dir, config.expected_holdout_questions),
        )
    )
    canary_ids = cast(set[str], sources[0]["question_ids"])
    holdout_ids = cast(set[str], sources[1]["question_ids"])
    if canary_ids & holdout_ids:
        raise ValueError("LoCoMo retrieval canary and holdout must be disjoint")
    target_ids = set(target.question_ids)
    if canary_ids | holdout_ids != target_ids:
        raise ValueError("LoCoMo retrieval gate sources do not cover the target question set")
    if (
        sources[0].get("selection_id") != _string(frozen_canary, "selection_id")
        or sources[0].get("question_set_sha256") != _string(frozen_canary, "question_set_sha256")
        or sources[0].get("selection_sha256") != _string(frozen_canary, "selection_sha256")
    ):
        raise ValueError("LoCoMo retrieval gate canary is not the frozen selection")
    if (
        sources[1].get("selection_id") != _string(frozen_holdout, "selection_id")
        or sources[1].get("question_set_sha256") != _string(frozen_holdout, "question_set_sha256")
        or sources[1].get("selection_sha256") != _string(frozen_holdout, "selection_sha256")
    ):
        raise ValueError("LoCoMo retrieval gate holdout is not the frozen selection")
    scored_ids = set(scored.question_ids)
    authorized_scored_definitions = {
        target.definition_sha256,
        _string(frozen_canary, "question_set_sha256"),
    }
    if (
        not scored_ids
        or not scored_ids.issubset(target_ids)
        or scored.definition_sha256 not in authorized_scored_definitions
    ):
        raise ValueError("LoCoMo scored question set is not the frozen canary or target")

    public_sources = [
        {key: value for key, value in source.items() if key != "question_ids"} for source in sources
    ]
    receipt: dict[str, object] = {
        "schema_version": 1,
        "contract": LOCOMO_PAID_SCORING_GATE_CONTRACT,
        "repository_commit": config.repository_commit,
        "dataset_sha256": dataset.sha256,
        "target_question_set_sha256": target.definition_sha256,
        "target_selection_sha256": target.selection_sha256,
        "target_question_count": len(target_ids),
        "scored_question_set_sha256": scored.definition_sha256,
        "scored_selection_sha256": scored.selection_sha256,
        "scored_question_count": len(scored_ids),
        "protocol_sha256": protocol_sha256,
        "corpus_content_sha256": corpus_content_sha256,
        "query_vectors_content_sha256": query_content_sha256,
        "minimum_context_all_coverage": config.minimum_context_all_coverage,
        "maximum_context_tokens": config.maximum_context_tokens,
        "maximum_retrieval_p95_ms": config.maximum_retrieval_p95_ms,
        "maximum_process_rss_bytes_exclusive": (config.maximum_process_rss_bytes_exclusive),
        "sources": public_sources,
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return validate_locomo_paid_scoring_receipt(
        receipt,
        repository_commit=config.repository_commit,
        dataset_sha256=dataset.sha256,
        scored_question_set_sha256=scored.definition_sha256,
        scored_selection_sha256=scored.selection_sha256,
        scored_question_count=len(scored_ids),
        protocol_sha256=protocol_sha256,
        corpus_content_sha256=corpus_content_sha256,
        query_vectors_content_sha256=query_content_sha256,
    )


def validate_locomo_paid_scoring_receipt(
    receipt: object,
    *,
    repository_commit: str,
    dataset_sha256: str,
    scored_question_set_sha256: str,
    scored_selection_sha256: str,
    scored_question_count: int,
    protocol_sha256: str,
    corpus_content_sha256: str,
    query_vectors_content_sha256: str,
) -> dict[str, object]:
    """Validate the immutable bindings a paid worker is allowed to trust."""

    observed = _mapping(receipt, field="LoCoMo paid-scoring preflight")
    if set(observed) != _RECEIPT_FIELDS:
        raise ValueError("LoCoMo paid-scoring preflight receipt schema does not match")
    receipt_sha256 = _sha256(observed, "receipt_sha256")
    body = {key: value for key, value in observed.items() if key != "receipt_sha256"}
    if (
        observed.get("schema_version") != 1
        or observed.get("contract") != LOCOMO_PAID_SCORING_GATE_CONTRACT
        or _canonical_sha256(body) != receipt_sha256
        or observed.get("repository_commit") != repository_commit
        or observed.get("dataset_sha256") != dataset_sha256
        or observed.get("scored_question_set_sha256") != scored_question_set_sha256
        or observed.get("scored_selection_sha256") != scored_selection_sha256
        or observed.get("scored_question_count") != scored_question_count
        or observed.get("protocol_sha256") != protocol_sha256
        or observed.get("corpus_content_sha256") != corpus_content_sha256
        or observed.get("query_vectors_content_sha256") != query_vectors_content_sha256
    ):
        raise ValueError("LoCoMo paid-scoring preflight receipt does not match")
    for field in (
        "dataset_sha256",
        "target_question_set_sha256",
        "target_selection_sha256",
        "scored_question_set_sha256",
        "scored_selection_sha256",
        "protocol_sha256",
        "corpus_content_sha256",
        "query_vectors_content_sha256",
    ):
        _sha256(observed, field)
    target_question_count = _positive_integer(observed, "target_question_count")
    observed_scored_count = _positive_integer(observed, "scored_question_count")
    if observed_scored_count > target_question_count:
        raise ValueError("LoCoMo paid-scoring preflight scored inventory exceeds its target")
    minimum_coverage = _number(observed, "minimum_context_all_coverage")
    maximum_tokens = _positive_integer(observed, "maximum_context_tokens")
    maximum_p95_ms = _positive_number(observed, "maximum_retrieval_p95_ms")
    maximum_rss = _positive_integer(
        observed,
        "maximum_process_rss_bytes_exclusive",
    )
    if not 0 < minimum_coverage <= 1:
        raise ValueError("LoCoMo paid-scoring preflight coverage threshold is invalid")
    raw_sources = observed.get("sources")
    if not isinstance(raw_sources, list) or len(raw_sources) != 2:
        raise ValueError("LoCoMo paid-scoring preflight requires exactly two retrieval sources")
    sources = [
        _mapping(source, field="LoCoMo paid-scoring retrieval source") for source in raw_sources
    ]
    if any(set(source) != _RECEIPT_SOURCE_FIELDS for source in sources):
        raise ValueError("LoCoMo paid-scoring retrieval source schema does not match")
    if (
        len({_string(source, "run_id") for source in sources}) != 2
        or len({_string(source, "selection_id") for source in sources}) != 2
    ):
        raise ValueError("LoCoMo paid-scoring retrieval sources are not distinct")
    source_question_count = 0
    for source in sources:
        for field in (
            "question_set_sha256",
            "selection_sha256",
            "manifest_sha256",
            "summary_sha256",
            "evidence_report_sha256",
            "resource_usage_sha256",
        ):
            _sha256(source, field)
        source_question_count += _positive_integer(source, "question_count")
        source_coverage = _number(source, "context_all_coverage")
        source_tokens = _integer(source, "maximum_context_tokens")
        source_p95_ms = _number(source, "retrieval_p95_ms")
        source_rss = _integer(source, "max_process_rss_bytes")
        if (
            not 0 <= source_coverage <= 1
            or source_coverage < minimum_coverage
            or source_tokens > maximum_tokens
            or source_p95_ms < 0
            or source_p95_ms > maximum_p95_ms
            or source_rss >= maximum_rss
        ):
            raise ValueError("LoCoMo paid-scoring retrieval source fails its frozen gate")
    if source_question_count != target_question_count:
        raise ValueError("LoCoMo paid-scoring retrieval sources do not cover the target")
    return observed


def _verify_source_run(
    run_dir: Path,
    *,
    dataset_path: Path,
    expected_question_count: int,
    repository_commit: str,
    protocol: dict[str, object],
    protocol_sha256: str,
    corpus_content_sha256: str,
    query_content_sha256: str,
    minimum_context_all_coverage: float,
    maximum_context_tokens: int,
    maximum_retrieval_p95_ms: float,
    maximum_process_rss_bytes_exclusive: int,
    reporter: LoCoMoRetrievalGateReporter,
) -> dict[str, object]:
    manifest_path = run_dir / "manifest.json"
    summary_path = run_dir / "summary.json"
    resource_path = run_dir / "resource-usage.json"
    manifest = _mapping(read_json(manifest_path), field="LoCoMo retrieval manifest")
    saved_summary = _mapping(read_json(summary_path), field="LoCoMo saved summary")
    summary = reporter.report(run_dir)
    evidence = reporter.evidence(run_dir, dataset_path=dataset_path)
    resource = _mapping(read_json(resource_path), field="LoCoMo resource usage")
    if saved_summary != summary:
        raise ValueError("LoCoMo retrieval gate saved summary does not match replay")

    if (
        manifest.get("mode") != "retrieval"
        or manifest.get("scored") is not False
        or manifest.get("answer_model") is not None
        or manifest.get("judge_model") is not None
        or summary.get("mode") != "retrieval"
        or summary.get("scored") is not False
    ):
        raise ValueError("LoCoMo retrieval gate source attempted model scoring")
    validate_locomo_retrieval_manifest_protocol(
        manifest,
        protocol=protocol,
    )
    if manifest.get("repository_commit") != repository_commit:
        raise ValueError("LoCoMo retrieval gate source commit does not match")
    planner = _mapping(
        _mapping(manifest.get("retrieval"), field="LoCoMo retrieval config").get("planner"),
        field="LoCoMo retrieval planner",
    )
    if planner.get("mode") != "hierarchy":
        raise ValueError("LoCoMo retrieval gate requires hierarchy mode")
    if (
        _mapping(manifest.get("corpus"), field="LoCoMo run corpus").get("content_sha256")
        != corpus_content_sha256
        or _mapping(
            manifest.get("query_vectors"),
            field="LoCoMo run query vectors",
        ).get("content_sha256")
        != query_content_sha256
    ):
        raise ValueError("LoCoMo retrieval gate source artifact binding does not match")

    question_set = _mapping(
        _mapping(manifest.get("selection"), field="LoCoMo run selection").get("question_set"),
        field="LoCoMo run question set",
    )
    question_ids = _string_set(question_set.get("question_ids"), field="question IDs")
    observed_selection_sha256 = hashlib.sha256(
        json.dumps(
            sorted(question_ids),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if (
        question_set.get("protocol_sha256") != protocol_sha256
        or question_set.get("question_count") != expected_question_count
        or question_set.get("selection_sha256") != observed_selection_sha256
        or len(question_ids) != expected_question_count
        or summary.get("completed_question_count") != expected_question_count
        or summary.get("question_artifact_count") != expected_question_count
    ):
        raise ValueError("LoCoMo retrieval gate source question inventory does not match")
    selection_id = _string(question_set, "selection_id")
    question_set_sha256 = _string(question_set, "definition_sha256")
    selection_sha256 = _string(question_set, "selection_sha256")
    if summary.get("infrastructure_failed_count") != 0:
        raise ValueError("LoCoMo retrieval gate source has infrastructure failures")
    usage = _mapping(summary.get("usage"), field="LoCoMo retrieval usage")
    if (
        usage.get("input_tokens") != 0
        or usage.get("output_tokens") != 0
        or usage.get("known_cost_count") != 0
        or usage.get("cost_usd") not in {None, 0}
    ):
        raise ValueError("LoCoMo retrieval gate source has non-zero model usage")

    overall = _mapping(evidence.get("overall"), field="LoCoMo evidence aggregate")
    context_coverage = _number(overall, "context_all_coverage")
    if context_coverage < minimum_context_all_coverage:
        raise ValueError("LoCoMo retrieval gate context coverage is below threshold")
    raw_questions = evidence.get("questions")
    if not isinstance(raw_questions, list) or len(raw_questions) != expected_question_count:
        raise ValueError("LoCoMo retrieval gate evidence question inventory does not match")
    evidence_question_ids = {
        _string(
            _mapping(question, field="LoCoMo evidence question"),
            "question_id",
        )
        for question in raw_questions
    }
    if (
        len(evidence_question_ids) != expected_question_count
        or evidence_question_ids != question_ids
    ):
        raise ValueError("LoCoMo retrieval gate evidence question IDs do not match")
    token_counts = tuple(
        _integer(
            _mapping(question, field="LoCoMo evidence question"),
            "context_token_count",
        )
        for question in raw_questions
    )
    if any(token_count > maximum_context_tokens for token_count in token_counts):
        raise ValueError("LoCoMo retrieval gate context token limit was exceeded")

    diagnostics = _mapping(
        summary.get("retrieval_diagnostics"),
        field="LoCoMo retrieval diagnostics",
    )
    latency = _mapping(diagnostics.get("latency_ms"), field="LoCoMo retrieval latency")
    p95_ms = _number(latency, "p95")
    if p95_ms > maximum_retrieval_p95_ms:
        raise ValueError("LoCoMo retrieval gate P95 latency is above threshold")
    summary_resources = _mapping(
        summary.get("worker_resources"),
        field="LoCoMo worker resources",
    )
    if resource != summary_resources:
        raise ValueError("LoCoMo retrieval gate resource artifact does not match")
    max_rss_bytes = _integer(resource, "max_process_rss_bytes")
    if max_rss_bytes >= maximum_process_rss_bytes_exclusive:
        raise ValueError("LoCoMo retrieval gate RSS limit was reached")

    evidence_sha256 = _canonical_sha256(evidence)
    return {
        "run_id": _string(manifest, "run_id"),
        "selection_id": selection_id,
        "question_set_sha256": question_set_sha256,
        "selection_sha256": selection_sha256,
        "question_count": len(question_ids),
        "context_all_coverage": context_coverage,
        "maximum_context_tokens": max(token_counts, default=0),
        "retrieval_p95_ms": p95_ms,
        "max_process_rss_bytes": max_rss_bytes,
        "manifest_sha256": file_sha256(manifest_path),
        "summary_sha256": file_sha256(summary_path),
        "evidence_report_sha256": evidence_sha256,
        "resource_usage_sha256": file_sha256(resource_path),
        "question_ids": question_ids,
    }


def _mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _string(value: dict[str, object], field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{field} must be non-empty text")
    return raw


def _string_set(value: object, *, field: str) -> set[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(cast(list[str], value)))
    ):
        raise ValueError(f"{field} must contain unique non-empty text")
    return set(cast(list[str], value))


def _number(value: dict[str, object], field: str) -> float:
    raw = value.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ValueError(f"{field} must be finite numeric")
    return float(raw)


def _integer(value: dict[str, object], field: str) -> int:
    raw = value.get(field)
    if type(raw) is not int or raw < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return raw


def _positive_integer(value: dict[str, object], field: str) -> int:
    raw = _integer(value, field)
    if raw < 1:
        raise ValueError(f"{field} must be positive")
    return raw


def _positive_number(value: dict[str, object], field: str) -> float:
    raw = _number(value, field)
    if raw <= 0:
        raise ValueError(f"{field} must be positive")
    return raw


def _sha256(value: dict[str, object], field: str) -> str:
    raw = _string(value, field)
    if _SHA256.fullmatch(raw) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return raw


def _canonical_sha256(value: object) -> str:
    return canonical_sha256(value)
