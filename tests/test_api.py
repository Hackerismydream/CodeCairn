from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from codecairn.bootstrap import create_application
from codecairn.entrypoints.api import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"


def test_api_doctor_and_empty_memory_list(tmp_path: Path) -> None:
    application = create_application(tmp_path / "runtime")
    client = TestClient(
        create_app(
            application,
            source_roots=(tmp_path,),
            artifact_root=tmp_path / "artifacts",
        )
    )

    doctor = client.get("/api/v1/health")
    assert doctor.status_code == 200
    assert doctor.json()["status"] == "ok"

    memories = client.get("/api/v1/memories", params={"repo_key": "acme/widgets"})
    assert memories.status_code == 200
    assert memories.json() == []


def test_api_finalize_and_typed_source_rewrite(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    source.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    client = TestClient(
        create_app(
            create_application(tmp_path / "runtime"),
            source_roots=(tmp_path,),
            artifact_root=tmp_path / "artifacts",
        )
    )
    request = {
        "source_path": str(source),
        "repo_key": "acme/widgets",
        "finalize": True,
        "index": False,
    }

    imported = client.post("/api/v1/import", json=request)
    assert imported.status_code == 200
    assert imported.json()["created_memory_count"] == 1
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "Run the repository test suite.",
            "Rewrite the committed task.",
        ),
        encoding="utf-8",
    )

    rewritten = client.post("/api/v1/import", json=request)
    assert rewritten.status_code == 422
    assert rewritten.json()["error"]["code"] == "source_rewritten"
