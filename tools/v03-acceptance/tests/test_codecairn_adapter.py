from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from codecairn_v03_acceptance.adapters.codecairn import CodeCairnAdapterError, CodeCairnPublicCLI, derive_new_pico_task_experience_ids

REPO_KEY = "local/v03-acceptance"
LEARN_SESSION = "cli:v03-learn-001"
QUERY = "上次为什么修改重试次数？"
PRIOR_ID = "mem_" + "1" * 64
CAPTURED_ID = "mem_" + "2" * 64
WRONG_SESSION_ID = "mem_" + "3" * 64


def test_public_cli_derives_exact_pico_capture_and_proves_fresh_recall(tmp_path: Path) -> None:
    prior = _memory(PRIOR_ID, memory_type="repository_knowledge", origin="agent_asserted", evidence=[])
    wrong_session = _memory(
        WRONG_SESSION_ID,
        memory_type="task_experience",
        origin="capture",
        evidence=[{"provider": "pico", "session_id": "cli:v03-learn-other"}],
    )
    captured = _memory(
        CAPTURED_ID, memory_type="task_experience", origin="capture", evidence=[{"provider": "pico", "session_id": LEARN_SESSION}]
    )
    recall = _recall(CAPTURED_ID)
    cli = _fake_cli(tmp_path, list_outputs=[[prior], [prior, wrong_session, captured]], recall_output=recall)
    evidence_dir = tmp_path / "evidence"

    before = cli.list_memories(
        artifact_path=evidence_dir / "list-before.json",
        source_environment={"PATH": os.environ["PATH"], "PYTHONPATH": "/source-checkout", "UNRELATED_SECRET": "do-not-pass"},
    )
    after = cli.list_memories(
        artifact_path=evidence_dir / "list-after.json",
        source_environment={"PATH": os.environ["PATH"], "PYTHONPATH": "/source-checkout", "UNRELATED_SECRET": "do-not-pass"},
    )
    captured_ids = derive_new_pico_task_experience_ids(before, after, repo_key=REPO_KEY, learn_session_id=LEARN_SESSION)
    receipt = cli.recall(
        QUERY,
        expected_memory_ids=set(captured_ids),
        artifact_path=evidence_dir / "recall.json",
        source_environment={"PATH": os.environ["PATH"], "PYTHONPATH": "/source-checkout", "UNRELATED_SECRET": "do-not-pass"},
    )

    assert captured_ids == (CAPTURED_ID,)
    assert receipt.repo_key == REPO_KEY
    assert receipt.query == QUERY
    assert receipt.source_cursor == receipt.index_cursor == 9
    assert receipt.ranked_memory_ids == (CAPTURED_ID,)
    assert receipt.rendered_memory_ids == (CAPTURED_ID,)
    assert receipt.recalled_memory_ids == (CAPTURED_ID,)
    assert receipt.source_uris == (f"codecairn://memory/{CAPTURED_ID}",)
    assert json.loads(before.artifact.path.read_text(encoding="utf-8")) == [prior]
    assert json.loads(after.artifact.path.read_text(encoding="utf-8")) == [prior, wrong_session, captured]
    assert hashlib.sha256(receipt.artifact.path.read_bytes()).hexdigest() == receipt.artifact.sha256
    assert receipt.artifact.path.stat().st_size == receipt.artifact.bytes

    with pytest.raises(FileExistsError):
        cli.recall(
            QUERY,
            expected_memory_ids={CAPTURED_ID},
            artifact_path=evidence_dir / "recall.json",
            source_environment={"PATH": os.environ["PATH"]},
        )

    wrong_only = replace(after, memories=(wrong_session,))
    with pytest.raises(CodeCairnAdapterError, match="capture_not_observed"):
        derive_new_pico_task_experience_ids(before, wrong_only, repo_key=REPO_KEY, learn_session_id=LEARN_SESSION)

    (cli.operator_dir / "codecairn.py").write_text("raise AssertionError\n", encoding="utf-8")
    with pytest.raises(ValueError, match="operator cwd"):
        cli.list_memories(artifact_path=evidence_dir / "dirty-list.json", source_environment={"PATH": os.environ["PATH"]})


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("stale", "recall_evidence_invalid"),
        ("cursor", "recall_evidence_invalid"),
        ("cursor_type", "recall_evidence_invalid"),
        ("abstained", "recall_evidence_invalid"),
        ("not_rendered", "expected_memory_not_recalled"),
        ("source_uri", "recall_evidence_invalid"),
    ],
)
def test_public_recall_rejects_unusable_evidence_but_preserves_raw_json(tmp_path: Path, mutation: str, error_code: str) -> None:
    payload = _recall(CAPTURED_ID)
    sidecar = payload["sidecar"]
    if mutation == "stale":
        sidecar["freshness"] = "semantic_pending"
    elif mutation == "cursor":
        sidecar["index_cursor"] = 8
    elif mutation == "cursor_type":
        sidecar["index_cursor"] = 9.0
    elif mutation == "abstained":
        sidecar["admission_trace"]["outcome"] = "abstained"
    elif mutation == "not_rendered":
        sidecar["context_trace"]["rendered_memory_ids"] = [PRIOR_ID]
        sidecar["ranked"].append({"memory_id": PRIOR_ID, "source_uri": f"codecairn://memory/{PRIOR_ID}"})
    else:
        sidecar["ranked"][0]["source_uri"] = "codecairn://memory/mismatched"
    case_dir = tmp_path / mutation
    cli = _fake_cli(case_dir, list_outputs=[[]], recall_output=payload)
    artifact = case_dir / "evidence" / "recall.json"

    with pytest.raises(CodeCairnAdapterError) as failure:
        cli.recall(QUERY, expected_memory_ids={CAPTURED_ID}, artifact_path=artifact, source_environment={"PATH": os.environ["PATH"]})

    assert failure.value.code == error_code
    assert json.loads(artifact.read_text(encoding="utf-8")) == payload


def _fake_cli(root: Path, *, list_outputs: list[list[dict[str, object]]], recall_output: dict[str, object]) -> CodeCairnPublicCLI:
    executable = root / "venv" / "bin" / "codecairn"
    operator_dir = root / "operator"
    config = root / "binding.toml"
    runtime_root = root / "runtime"
    executable.parent.mkdir(parents=True)
    operator_dir.mkdir(parents=True)
    runtime_root.mkdir(parents=True)
    config.write_text("schema_version = 1\n", encoding="utf-8")
    list_command = ["list", "--repo-key", REPO_KEY, "--root", str(runtime_root.resolve()), "--config", str(config.resolve())]
    recall_command = [
        "recall",
        QUERY,
        "--repo-key",
        REPO_KEY,
        "--root",
        str(runtime_root.resolve()),
        "--config",
        str(config.resolve()),
        "--limit",
        "5",
        "--format",
        "json",
    ]
    executable.write_text(
        f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

LIST_OUTPUTS = {list_outputs!r}
RECALL_OUTPUT = {recall_output!r}
EXPECTED_OPERATOR = {str(operator_dir.resolve())!r}
EXPECTED_LIST = {list_command!r}
EXPECTED_RECALL = {recall_command!r}

if (
    str(Path.cwd()) != EXPECTED_OPERATOR
    or os.environ.get("PYTHONPATH") != ""
    or os.environ.get("PYTHONNOUSERSITE") != "1"
    or "UNRELATED_SECRET" in os.environ
):
    raise SystemExit(91)
arguments = sys.argv[1:]
if arguments == EXPECTED_LIST:
    state = Path(__file__).with_suffix(".count")
    count = int(state.read_text()) if state.exists() else 0
    state.write_text(str(count + 1))
    print(json.dumps(LIST_OUTPUTS[min(count, len(LIST_OUTPUTS) - 1)], sort_keys=True))
    raise SystemExit(0)
if arguments == EXPECTED_RECALL:
    print(json.dumps(RECALL_OUTPUT, sort_keys=True))
    raise SystemExit(0)
raise SystemExit(92)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return CodeCairnPublicCLI(
        executable=executable, operator_dir=operator_dir, config=config, runtime_root=runtime_root, repo_key=REPO_KEY
    )


def _memory(memory_id: str, *, memory_type: str, origin: str, evidence: list[dict[str, str]]) -> dict[str, object]:
    return {"evidence": evidence, "memory_id": memory_id, "memory_type": memory_type, "origin": origin, "repo_key": REPO_KEY}


def _recall(memory_id: str) -> dict[str, object]:
    return {
        "markdown": "public context",
        "sidecar": {
            "admission_trace": {"outcome": "admitted"},
            "context_trace": {"rendered_memory_ids": [memory_id]},
            "freshness": "fresh",
            "include_superseded": False,
            "index_cursor": 9,
            "limit": 5,
            "query": QUERY,
            "ranked": [{"memory_id": memory_id, "source_uri": f"codecairn://memory/{memory_id}"}],
            "repo_key": REPO_KEY,
            "source_cursor": 9,
        },
    }
