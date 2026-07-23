import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codecairn.bootstrap import create_application, create_cascade, create_runtime
from codecairn.entrypoints.api import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"
CLAUDE_FIXTURE = Path(__file__).parent / "fixtures" / "claude" / "failed_command.jsonl"


def test_http_import_and_list_share_the_runtime_contract(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            create_application(tmp_path / "runtime"),
            source_roots=(FIXTURE.parent,),
            artifact_root=tmp_path / "artifacts",
        )
    )

    imported = client.post(
        "/api/v1/import",
        json={"source_path": str(FIXTURE), "repo_key": "acme/widgets"},
    )

    assert imported.status_code == 200
    assert imported.json()["created_memory_count"] == 1

    listed = client.get(
        "/api/v1/memories",
        params={"repo_key": "acme/widgets"},
    )
    assert listed.status_code == 200
    memories = listed.json()
    assert len(memories) == 1
    assert memories[0]["command"] == "uv run pytest"
    assert [item["raw_event_index"] for item in memories[0]["evidence"]] == [2, 3]
    assert "markdown_path" not in memories[0]
    assert "source_path" not in memories[0]["evidence"][0]


def test_http_import_auto_detects_claude_code(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            create_application(tmp_path / "runtime"),
            source_roots=(CLAUDE_FIXTURE.parent,),
            artifact_root=tmp_path / "artifacts",
        )
    )

    imported = client.post(
        "/api/v1/import",
        json={"source_path": str(CLAUDE_FIXTURE), "repo_key": "acme/widgets"},
    )

    assert imported.status_code == 200
    assert imported.json()["provider"] == "claude"
    assert imported.json()["created_memory_count"] == 1


def test_http_import_rejects_source_outside_configured_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    client = TestClient(
        create_app(
            create_application(tmp_path / "runtime"),
            source_roots=(allowed,),
            artifact_root=tmp_path / "artifacts",
        )
    )

    response = client.post(
        "/api/v1/import",
        json={"source_path": str(outside), "repo_key": "acme/widgets"},
    )

    assert response.status_code == 403
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert response.json()["error"]["code"] == "source_path_forbidden"


def test_http_import_rejects_intermediate_symlink_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    source = outside / "session.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    (allowed / "escape").symlink_to(outside, target_is_directory=True)
    client = TestClient(
        create_app(
            create_application(tmp_path / "runtime"),
            source_roots=(allowed,),
            artifact_root=tmp_path / "artifacts",
        )
    )

    response = client.post(
        "/api/v1/import",
        json={
            "source_path": str(allowed / "escape" / "session.jsonl"),
            "repo_key": "acme/widgets",
        },
    )

    assert response.status_code == 422
    assert "symbolic links" in response.json()["error"]["message"]


def test_http_recall_uses_the_shared_ranked_context_contract(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    runtime = create_runtime(root)
    runtime.import_session(FIXTURE, repo_key="acme/widgets", source_root=FIXTURE.parent)
    create_cascade(root).run_until_idle(worker_id="test")
    client = TestClient(
        create_app(
            create_application(root),
            source_roots=(FIXTURE.parent,),
            artifact_root=tmp_path / "artifacts",
        )
    )

    response = client.post(
        "/api/v1/recall",
        json={
            "task": "pytest command failed",
            "repo_key": "acme/widgets",
            "limit": 5,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["markdown"].startswith("# Recall Context")
    assert result["sidecar"]["ranked"][0]["candidate_sources"] == ["lexical", "vector"]
    evidence = result["sidecar"]["ranked"][0]["evidence"][0]
    assert "source_path" not in evidence
    assert evidence["raw_event_index"] == 2


def test_http_health_and_evaluation_routes_share_application_use_cases(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    client = TestClient(
        create_app(
            create_application(tmp_path / "runtime"),
            source_roots=(FIXTURE.parent,),
            artifact_root=artifact_root,
        )
    )

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["markdown_truth"]["ready"] is True
    assert health.json()["import_ledger"]["import_count"] == 0
    assert health.json()["index_queue"]["pending"] == 0
    assert health.json()["index"]["ready"] is True
    assert "openai_compatible" in health.json()["providers"]

    executed = client.post(
        "/api/v1/evaluations",
        json={
            "suite": "recovery",
            "input_path": str(FIXTURE),
            "run_id": "api-recovery",
            "repository_commit": "abc123",
        },
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["all_passed"] is True

    reported = client.get("/api/v1/evaluations/recovery/api-recovery")
    assert reported.status_code == 200, reported.text
    assert reported.json() == executed.json()


def test_http_has_six_versioned_routes_and_stable_validation_errors(tmp_path: Path) -> None:
    app = create_app(
        create_application(tmp_path / "runtime"),
        source_roots=(FIXTURE.parent,),
        artifact_root=tmp_path / "artifacts",
    )
    client = TestClient(app)

    paths = {path for path in app.openapi()["paths"] if path.startswith("/api/v1/")}
    assert paths == {
        "/api/v1/import",
        "/api/v1/memories",
        "/api/v1/recall",
        "/api/v1/evaluations",
        "/api/v1/evaluations/{suite}/{run_id}",
        "/api/v1/health",
    }
    response = client.post(
        "/api/v1/recall",
        json={"task": "", "repo_key": "", "limit": 100},
        headers={"x-request-id": "caller-request-123"},
    )
    assert response.status_code == 422
    assert response.headers["x-request-id"] == "caller-request-123"
    assert response.json()["request_id"] == "caller-request-123"
    assert response.json()["error"]["code"] == "validation_error"


def test_http_logs_request_id_and_keeps_provider_secrets_out_of_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("CODECAIRN_OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("CODECAIRN_OPENAI_API_KEY", "must-not-appear")
    monkeypatch.setenv("CODECAIRN_OPENAI_MODEL", "fixed-model")
    client = TestClient(
        create_app(
            create_application(tmp_path / "runtime"),
            source_roots=(FIXTURE.parent,),
            artifact_root=tmp_path / "artifacts",
        )
    )

    with caplog.at_level(logging.INFO, logger="codecairn.api"):
        response = client.get(
            "/api/v1/health",
            headers={"x-request-id": "health-request-123"},
        )

    assert response.status_code == 200
    assert response.json()["providers"]["openai_compatible"]["configured"] is True
    assert "must-not-appear" not in response.text
    assert any(
        getattr(record, "request_id", None) == "health-request-123" for record in caplog.records
    )


def test_http_health_recognizes_deepseek_role_defaults_without_exposing_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-must-not-appear")
    client = TestClient(
        create_app(
            create_application(tmp_path / "runtime"),
            source_roots=(FIXTURE.parent,),
            artifact_root=tmp_path / "artifacts",
        )
    )

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    provider = response.json()["providers"]["openai_compatible"]
    assert provider == {
        "configured": True,
        "answer_configured": True,
        "judge_configured": True,
    }
    assert "deepseek-must-not-appear" not in response.text


def test_http_rejects_remote_bind_and_evaluation_symlink_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="trusted loopback"):
        create_app(
            create_application(tmp_path / "remote-runtime"),
            source_roots=(FIXTURE.parent,),
            artifact_root=tmp_path / "remote-artifacts",
            bind_host="0.0.0.0",
        )

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    source = outside / "source.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    (allowed / "escape.jsonl").symlink_to(source)
    client = TestClient(
        create_app(
            create_application(tmp_path / "runtime"),
            source_roots=(allowed,),
            artifact_root=tmp_path / "artifacts",
        )
    )

    response = client.post(
        "/api/v1/evaluations",
        json={
            "suite": "recovery",
            "input_path": str(allowed / "escape.jsonl"),
            "run_id": "escape",
            "repository_commit": "abc123",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "source_path_forbidden"

    allowed_source = allowed / "source.jsonl"
    allowed_source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    response = client.post(
        "/api/v1/evaluations",
        json={
            "suite": "locomo",
            "input_path": str(allowed_source),
            "run_id": "artifact-escape",
            "repository_commit": "abc123",
            "mode": "retrieval",
            "corpus_path": str(outside),
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "artifact_path_forbidden"

    gate_question_set = allowed / "gate-question-set.json"
    gate_question_set.write_text("{}", encoding="utf-8")
    response = client.post(
        "/api/v1/evaluations",
        json={
            "suite": "recovery",
            "input_path": str(allowed_source),
            "run_id": "ignored-gate-question-set",
            "repository_commit": "abc123",
            "retrieval_gate_question_set_path": str(gate_question_set),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_locomo_artifact"
