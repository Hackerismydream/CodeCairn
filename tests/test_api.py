from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from codecairn.bootstrap import create_application
from codecairn.entrypoints.api import create_app


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
