from pathlib import Path

from fastapi.testclient import TestClient

from codecairn.bootstrap import create_runtime
from codecairn.entrypoints.api import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "failed_command.jsonl"


def test_http_import_and_list_share_the_runtime_contract(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            create_runtime(tmp_path / "runtime"),
            source_roots=(FIXTURE.parent,),
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


def test_http_import_rejects_source_outside_configured_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    client = TestClient(
        create_app(
            create_runtime(tmp_path / "runtime"),
            source_roots=(allowed,),
        )
    )

    response = client.post(
        "/api/v1/import",
        json={"source_path": str(outside), "repo_key": "acme/widgets"},
    )

    assert response.status_code == 403


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
            create_runtime(tmp_path / "runtime"),
            source_roots=(allowed,),
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
    assert "symbolic links" in response.json()["detail"]
