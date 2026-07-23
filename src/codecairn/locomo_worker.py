from __future__ import annotations

import hashlib
import os
import resource
import signal
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import cast

from codecairn.bootstrap import create_cascade, create_retrieval_providers, create_runtime
from codecairn.evaluation.artifacts import file_sha256, read_json, write_json_exclusive
from codecairn.evaluation.locomo import (
    LOCOMO_PAID_SCORING_GATE_CONTRACT,
    CodeCairnConversationMemory,
    FrozenQueryEmbeddingAdapter,
    LoCoMoRunConfig,
    RunMode,
    load_locomo_dataset,
    run_locomo_conversation_questions,
    validate_locomo_corpus_conversation,
    validate_locomo_corpus_preflight,
)
from codecairn.evaluation.locomo_retrieval_gate import (
    validate_locomo_paid_scoring_receipt,
)
from codecairn.evaluation.providers import create_locomo_text_model


class _WorkerTermination(BaseException):
    pass


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m codecairn.locomo_worker SPEC.json")
    spec_path = Path(sys.argv[1]).resolve()
    raw = read_json(spec_path)
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise ValueError("LoCoMo worker spec is not supported")
    parent_pid = _integer(cast(dict[str, object], raw), "parent_pid")
    if os.getppid() != parent_pid:
        raise ValueError("LoCoMo worker parent process does not match its spec")
    worker_identity_path = Path(_string(raw, "worker_identity_path")).resolve()
    worker_identity = _wait_for_worker_identity(worker_identity_path, parent_pid=parent_pid)
    if (
        not isinstance(worker_identity, dict)
        or worker_identity.get("schema_version") != 1
        or worker_identity.get("pid") != os.getpid()
        or worker_identity.get("parent_pid") != parent_pid
        or worker_identity.get("spec_sha256") != file_sha256(spec_path)
    ):
        raise ValueError("LoCoMo worker identity does not match its process")
    signal.signal(signal.SIGTERM, _raise_worker_termination)
    resource_path = Path(_string(raw, "resource_path")).resolve()
    heartbeat_path = Path(_string(raw, "heartbeat_path")).resolve()
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.touch(exist_ok=False)
    heartbeat_stop = threading.Event()
    started = time.perf_counter()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(heartbeat_path, heartbeat_stop),
        name="locomo-worker-heartbeat",
        daemon=True,
    )
    heartbeat.start()
    parent_watchdog = threading.Thread(
        target=_watch_parent,
        args=(parent_pid, heartbeat_stop, cast(dict[str, object], raw), resource_path, started),
        name="locomo-worker-parent-watchdog",
        daemon=True,
    )
    parent_watchdog.start()
    status = "completed"
    error_type: str | None = None
    try:
        _execute(cast(dict[str, object], raw))
    except BaseException as caught:
        status = "failed"
        error_type = type(caught).__name__
        raise
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=2)
        parent_watchdog.join(timeout=2)
        _write_worker_receipt(
            cast(dict[str, object], raw),
            resource_path,
            status=status,
            error_type=error_type,
            started=started,
        )


def _execute(raw: dict[str, object]) -> None:
    dataset_path = Path(_string(raw, "dataset_path")).resolve()
    dataset = load_locomo_dataset(dataset_path)
    expected_dataset_sha256 = _string(raw, "dataset_sha256")
    if dataset.sha256 != expected_dataset_sha256:
        raise ValueError("LoCoMo worker dataset digest does not match")
    conversation_id = _string(raw, "conversation_id")
    conversation = next(
        (item for item in dataset.conversations if item.sample_id == conversation_id),
        None,
    )
    if conversation is None:
        raise ValueError("LoCoMo worker conversation does not exist")

    run_manifest_path = Path(_string(raw, "run_manifest_path")).resolve()
    if file_sha256(run_manifest_path) != _string(raw, "run_manifest_sha256"):
        raise ValueError("LoCoMo worker run manifest digest does not match")
    run_manifest = _mapping(read_json(run_manifest_path), "run manifest")
    if run_manifest.get("repository_commit") != _string(raw, "repository_commit"):
        raise ValueError("LoCoMo worker repository commit does not match")
    mode = _run_mode(raw)
    _validate_worker_paid_scoring_preflight(raw, run_manifest, mode=mode)
    question_ids = _string_list(raw, "question_ids")
    selection = _mapping(run_manifest.get("selection"), "run selection")
    inventory = _mapping(selection.get("question_ids_by_conversation"), "run question inventory")
    if inventory.get(conversation_id) != question_ids:
        raise ValueError("LoCoMo worker question inventory does not match the run")

    corpus_dir = Path(_string(raw, "corpus_dir")).resolve()
    corpus_manifest = _mapping(read_json(corpus_dir / "manifest.json"), "corpus manifest")
    corpus_build_contract = _mapping(
        corpus_manifest.get("build_contract"),
        "corpus build contract",
    )
    corpus_semantic_projection = _mapping(
        corpus_build_contract.get("semantic_projection"),
        "corpus semantic projection",
    )
    expected_corpus_repository_commit = _string(raw, "corpus_repository_commit")
    if (
        corpus_manifest.get("repository_commit") != expected_corpus_repository_commit
        or corpus_build_contract.get("repository_commit") != expected_corpus_repository_commit
    ):
        raise ValueError("LoCoMo worker corpus repository commit does not match")
    expected_corpus_content_sha256 = _string(raw, "corpus_content_sha256")
    if corpus_manifest.get("content_sha256") != expected_corpus_content_sha256:
        raise ValueError("LoCoMo worker corpus manifest does not match")
    run_corpus = _mapping(run_manifest.get("corpus"), "run corpus")
    if (
        run_corpus.get("repository_commit") != expected_corpus_repository_commit
        or run_corpus.get("content_sha256") != expected_corpus_content_sha256
        or run_corpus.get("tree_sha256") != _string(raw, "corpus_tree_sha256")
    ):
        raise ValueError("LoCoMo worker corpus binding does not match")
    expected_retrieval = _mapping(raw.get("retrieval_config"), "retrieval config")
    validate_locomo_corpus_preflight(
        corpus_dir,
        dataset=dataset,
        expected_content_sha256=expected_corpus_content_sha256,
        retrieval_config=expected_retrieval,
    )

    query_vectors_path = Path(_string(raw, "query_vectors_path")).resolve()
    query_manifest = _mapping(
        read_json(query_vectors_path / "manifest.json"), "query vector manifest"
    )
    expected_query_content_sha256 = _string(raw, "query_vectors_content_sha256")
    if query_manifest.get("content_sha256") != expected_query_content_sha256:
        raise ValueError("LoCoMo worker query vector manifest does not match")
    run_query_vectors = _mapping(run_manifest.get("query_vectors"), "run query vectors")
    if run_query_vectors.get("content_sha256") != expected_query_content_sha256:
        raise ValueError("LoCoMo worker query vector binding does not match")

    retrieval = replace(
        create_retrieval_providers(environment=os.environ),
        embedder=FrozenQueryEmbeddingAdapter(query_vectors_path),
    )
    if retrieval.public_config != expected_retrieval:
        raise ValueError("LoCoMo worker retrieval configuration does not match")
    run_retrieval = _mapping(run_manifest.get("retrieval"), "run retrieval config")
    if {key: value for key, value in run_retrieval.items() if key != "top_k"} != expected_retrieval:
        raise ValueError("LoCoMo worker run retrieval configuration does not match")
    warmup_started = time.perf_counter()
    retrieval.reranker.warmup()
    raw["reranker_warmup_ms"] = round(
        (time.perf_counter() - warmup_started) * 1_000,
        3,
    )

    expected_answer_model = _optional_mapping(raw.get("answer_model"), "answer model")
    answer_model = (
        None
        if mode == "retrieval"
        else create_locomo_text_model(
            role="answer",
            environment=os.environ,
            model_override=_model_name(expected_answer_model),
        )
    )
    if (None if answer_model is None else answer_model.public_config) != expected_answer_model:
        raise ValueError("LoCoMo worker answer model configuration does not match")
    if run_manifest.get("answer_model") != expected_answer_model:
        raise ValueError("LoCoMo worker run answer model configuration does not match")
    expected_judge_model = _optional_mapping(raw.get("judge_model"), "judge model")
    judge_model = (
        create_locomo_text_model(
            role="judge",
            environment=os.environ,
            model_override=_model_name(expected_judge_model),
        )
        if mode == "full"
        else None
    )
    if (None if judge_model is None else judge_model.public_config) != expected_judge_model:
        raise ValueError("LoCoMo worker judge model configuration does not match")
    if run_manifest.get("judge_model") != expected_judge_model:
        raise ValueError("LoCoMo worker run judge model configuration does not match")

    worker_run_dir = Path(_string(raw, "worker_run_dir")).resolve()
    worker_corpus_dir = Path(_string(raw, "worker_corpus_dir")).resolve()
    if not worker_corpus_dir.is_relative_to(worker_run_dir):
        raise ValueError("LoCoMo worker corpus copy escapes its staging directory")
    memory_root = worker_corpus_dir / "runtime" / conversation.sample_id

    def memory_factory(root: Path) -> CodeCairnConversationMemory:
        return CodeCairnConversationMemory(
            runtime=create_runtime(root, retrieval=retrieval),
            cascade=create_cascade(root, retrieval=retrieval),
            repo_key=f"locomo/{root.name}",
            semantic_projection=corpus_semantic_projection,
        )

    memory = cast(
        CodeCairnConversationMemory,
        validate_locomo_corpus_conversation(
            corpus_dir,
            conversation,
            expected_content_sha256=expected_corpus_content_sha256,
            memory_factory=memory_factory,
            runtime_root=memory_root,
        ),
    )

    def verified_memory_factory(root: Path) -> CodeCairnConversationMemory:
        if root.resolve() != memory_root.resolve():
            raise ValueError("LoCoMo worker requested an unverified runtime")
        return memory

    config = LoCoMoRunConfig(
        dataset_path=dataset_path,
        output_root=worker_run_dir.parent,
        run_id=worker_run_dir.name,
        repository_commit=_string(raw, "repository_commit"),
        mode=mode,
        categories=_integer_tuple(raw, "categories"),
        top_k=_integer(raw, "top_k"),
        judge_votes=_integer(raw, "judge_votes"),
        judge_response_max_attempts=_integer(raw, "judge_response_max_attempts"),
        judge_response_max_chars=_integer(raw, "judge_response_max_chars"),
        seed=_integer(raw, "seed"),
        max_workers=_integer(raw, "max_workers"),
        expected_dataset_sha256=expected_dataset_sha256,
        retrieval_config=retrieval.public_config,
        corpus_path=worker_corpus_dir,
        query_vectors_path=query_vectors_path,
        execution_phase="questions",
    )
    run_locomo_conversation_questions(
        _integer(raw, "conversation_index"),
        conversation,
        config=config,
        run_dir=worker_run_dir,
        corpus_dir=worker_corpus_dir,
        memory_factory=verified_memory_factory,
        answer_model=answer_model,
        judge_model=judge_model,
        selected_question_ids=set(question_ids),
    )


def _run_mode(raw: dict[str, object]) -> RunMode:
    value = _string(raw, "mode")
    if value not in {"full", "smoke", "retrieval"}:
        raise ValueError("LoCoMo worker mode is invalid")
    return cast(RunMode, value)


def _validate_worker_paid_scoring_preflight(
    raw: dict[str, object],
    run_manifest: dict[str, object],
    *,
    mode: RunMode,
) -> None:
    gate_contract = run_manifest.get("paid_scoring_gate")
    expected_receipt_sha256 = raw.get("paid_scoring_preflight_sha256")
    receipt = run_manifest.get("paid_scoring_preflight")
    if mode == "retrieval":
        if expected_receipt_sha256 is not None or receipt is not None:
            raise ValueError("LoCoMo retrieval mode must not carry a paid-scoring preflight")
        return
    if gate_contract is None:
        if expected_receipt_sha256 is not None or receipt is not None:
            raise ValueError("LoCoMo legacy worker must not carry a paid-scoring preflight")
        return
    if gate_contract != LOCOMO_PAID_SCORING_GATE_CONTRACT:
        raise ValueError("LoCoMo worker paid-scoring gate is not supported")
    if not isinstance(expected_receipt_sha256, str) or not expected_receipt_sha256:
        raise ValueError("LoCoMo worker paid-scoring preflight binding does not match")
    selection = _mapping(run_manifest.get("selection"), "run selection")
    question_set = _mapping(selection.get("question_set"), "run question set")
    corpus = _mapping(run_manifest.get("corpus"), "run corpus")
    query_vectors = _mapping(run_manifest.get("query_vectors"), "run query vectors")
    validated = validate_locomo_paid_scoring_receipt(
        receipt,
        repository_commit=_string(run_manifest, "repository_commit"),
        dataset_sha256=_string(question_set, "dataset_sha256"),
        scored_question_set_sha256=_string(question_set, "definition_sha256"),
        scored_selection_sha256=_string(question_set, "selection_sha256"),
        scored_question_count=_integer(question_set, "question_count"),
        protocol_sha256=_string(question_set, "protocol_sha256"),
        corpus_content_sha256=_string(corpus, "content_sha256"),
        query_vectors_content_sha256=_string(query_vectors, "content_sha256"),
    )
    if validated.get("receipt_sha256") != expected_receipt_sha256:
        raise ValueError("LoCoMo worker paid-scoring preflight binding does not match")


def _wait_for_worker_identity(path: Path, *, parent_pid: int) -> object:
    deadline = time.monotonic() + 5.0
    while not path.is_file():
        if os.getppid() != parent_pid:
            raise RuntimeError("LoCoMo worker parent exited before identity publication")
        if time.monotonic() >= deadline:
            raise TimeoutError("LoCoMo worker identity publication timed out")
        time.sleep(0.01)
    return read_json(path)


def _raise_worker_termination(_signum: int, _frame: object) -> None:
    raise _WorkerTermination("LoCoMo worker received SIGTERM")


def _heartbeat(path: Path, stop: threading.Event) -> None:
    while not stop.wait(1.0):
        path.touch(exist_ok=True)


def _watch_parent(
    parent_pid: int,
    stop: threading.Event,
    raw: dict[str, object],
    resource_path: Path,
    started: float,
) -> None:
    while not stop.wait(1.0):
        if os.getppid() != parent_pid:
            try:
                _write_worker_receipt(
                    raw,
                    resource_path,
                    status="parent_lost",
                    error_type=None,
                    started=started,
                )
            finally:
                os._exit(70)


def _write_worker_receipt(
    raw: dict[str, object],
    resource_path: Path,
    *,
    status: str,
    error_type: str | None,
    started: float,
) -> None:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss = int(usage.ru_maxrss)
    if sys.platform != "darwin":
        max_rss *= 1024
    question_dir = (
        Path(_string(raw, "worker_run_dir"))
        / "checkpoints"
        / "questions"
        / _string(raw, "conversation_id")
    )
    checkpoint_hashes = [(path, file_sha256(path)) for path in sorted(question_dir.glob("*.json"))]
    checkpoints = {path.stem: sha256 for path, sha256 in checkpoint_hashes}
    digest = hashlib.sha256()
    question_tree = sorted(question_dir.rglob("*"))
    if any(
        path.is_symlink() or not path.resolve().is_relative_to(question_dir)
        for path in question_tree
    ):
        raise ValueError("LoCoMo worker question artifact escapes its directory")
    for path in (item for item in question_tree if item.is_file()):
        digest.update(path.relative_to(question_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_sha256(path)))
    write_json_exclusive(
        resource_path,
        {
            "schema_version": 1,
            "status": status,
            "conversation_id": _string(raw, "conversation_id"),
            "parent_pid": _integer(raw, "parent_pid"),
            "pid": os.getpid(),
            "wall_time_seconds": round(time.perf_counter() - started, 6),
            "reranker_warmup_ms": raw.get("reranker_warmup_ms"),
            "max_rss_bytes": max_rss,
            "completed_question_checkpoints": checkpoints,
            "question_checkpoint_sha256": digest.hexdigest(),
            "error_type": error_type,
        },
    )


def _string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"LoCoMo worker field {key} must be a non-empty string")
    return value


def _integer(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if type(value) is not int:
        raise ValueError(f"LoCoMo worker field {key} must be an integer")
    return value


def _integer_tuple(raw: dict[str, object], key: str) -> tuple[int, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or any(type(item) is not int for item in value):
        raise ValueError(f"LoCoMo worker field {key} must be an integer list")
    return tuple(cast(list[int], value))


def _string_list(raw: dict[str, object], key: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"LoCoMo worker field {key} must be a non-empty string list")
    result = cast(list[str], value)
    if len(result) != len(set(result)):
        raise ValueError(f"LoCoMo worker field {key} contains duplicates")
    return result


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"LoCoMo worker {field} must be an object")
    return cast(dict[str, object], value)


def _optional_mapping(value: object, field: str) -> dict[str, object] | None:
    return None if value is None else _mapping(value, field)


def _model_name(config: dict[str, object] | None) -> str | None:
    if config is None:
        return None
    value = config.get("model")
    if not isinstance(value, str) or not value:
        raise ValueError("LoCoMo worker model configuration has no model name")
    return value


if __name__ == "__main__":
    main()
