from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

from codecairn.bootstrap import create_application
from codecairn.configuration import initialize_repository
from codecairn.entrypoints.mcp import build_server, schema_snapshot
from tests.retrieval_fakes import TEST_RETRIEVAL

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "codex" / "failed_command.jsonl"
SCHEMA = ROOT / "docs" / "schemas" / "mcp-v01.json"


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    runtime = tmp_path / "runtime"
    initialize_repository(start=repository, root=runtime, repo_key="acme/widgets", retrieval_profile="fastembed")
    return repository, runtime


def _server(repository: Path) -> Any:
    def factory(root: Path, **kwargs: Any) -> Any:
        return create_application(root, retrieval_adapters=TEST_RETRIEVAL, **kwargs)

    return build_server(factory, working_directory=repository)


def _structured(result: Any) -> dict[str, Any]:
    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    return result.structuredContent


def test_mcp_tools_resource_identity_pagination_and_errors(tmp_path: Path) -> None:
    repository, runtime = _repository(tmp_path)
    server = _server(repository)

    async def exercise() -> None:
        async with create_connected_server_and_client_session(server) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "recall",
                "remember",
                "list_memories",
                "get_memory",
                "memory_history",
                "import_session",
                "doctor",
            }
            assert all(tool.inputSchema.get("additionalProperties") is False for tool in tools.tools)
            templates = await client.list_resource_templates()
            assert [item.uriTemplate for item in templates.resourceTemplates] == ["codecairn://memory/{memory_id}"]

            knowledge = _structured(
                await client.call_tool(
                    "remember",
                    {
                        "memory_type": "repository_knowledge",
                        "title": "Repository checks",
                        "content": "Use pytest for repository checks.",
                        "subject_key": "repository checks",
                    },
                )
            )
            state = _structured(
                await client.call_tool(
                    "remember",
                    {
                        "memory_type": "work_state",
                        "title": "MCP delivery",
                        "content": "MCP implementation is in progress.",
                        "workstream_key": "task:mcp",
                        "goal": "Ship MCP",
                        "next_step": "Run acceptance tests",
                    },
                )
            )
            assert state["memory_type"] == "work_state"
            replacement = _structured(
                await client.call_tool(
                    "remember",
                    {
                        "memory_type": "repository_knowledge",
                        "title": "Current repository checks",
                        "content": "Run make check for repository checks.",
                        "subject_key": "repository checks",
                    },
                )
            )
            create_application(runtime, retrieval_adapters=TEST_RETRIEVAL).supersede(
                repo_key="acme/widgets",
                predecessor_id=knowledge["memory_id"],
                successor_id=replacement["memory_id"],
                reason="The repository command is more complete.",
                proposer="agent",
            )
            active = _structured(await client.call_tool("recall", {"task": "repository checks"}))
            assert knowledge["memory_id"] not in {item["memory_id"] for item in active["sidecar"]["ranked"]}
            historical = _structured(await client.call_tool("recall", {"task": "repository checks", "include_superseded": True}))
            assert any(
                item["memory_id"] == knowledge["memory_id"] and item["status"] == "superseded"
                for item in historical["sidecar"]["ranked"]
            )

            rejected = await client.call_tool(
                "remember",
                {"memory_type": "user_preference", "title": "No source", "content": "Use compact output.", "subject_key": "output"},
            )
            assert rejected.isError
            assert '"code":"schema_invalid"' in rejected.content[0].text
            experience = await client.call_tool(
                "remember",
                {"memory_type": "task_experience", "title": "Unsafe direct experience", "content": "Must come from an Episode."},
            )
            assert experience.isError

            imported = _structured(await client.call_tool("import_session", {"source_path": str(FIXTURE)}))
            assert imported["result"]["created_memory_count"] == 1
            recalled = _structured(await client.call_tool("recall", {"task": "pytest failure"}))
            assert recalled["markdown"]
            assert recalled["sidecar"]["ranked"]

            page = _structured(await client.call_tool("list_memories", {"limit": 1}))
            assert len(page["items"]) == 1
            assert page["next_cursor"]
            second = _structured(await client.call_tool("list_memories", {"limit": 100, "cursor": page["next_cursor"]}))
            assert second["items"]
            assert page["items"][0]["memory_id"] != second["items"][0]["memory_id"]

            detail = _structured(await client.call_tool("get_memory", {"memory_id": knowledge["memory_id"]}))
            history = _structured(await client.call_tool("memory_history", {"memory_id": knowledge["memory_id"]}))
            assert detail["memory"]["memory_id"] == knowledge["memory_id"]
            assert history["memories"][0]["memory_id"] == knowledge["memory_id"]
            foreign = await client.call_tool("get_memory", {"memory_id": knowledge["memory_id"], "repo_key": "other/repository"})
            assert foreign.isError
            assert '"code":"foreign_namespace"' in foreign.content[0].text

            resource = await client.read_resource(AnyUrl(f"codecairn://memory/{knowledge['memory_id']}"))
            assert knowledge["memory_id"] in resource.contents[0].text
            assert "Use pytest for repository checks." in resource.contents[0].text
            with pytest.raises(McpError):
                await client.read_resource(AnyUrl("codecairn://memory/mem_%2Funsafe"))

            task_page = _structured(await client.call_tool("list_memories", {"memory_type": "task_experience", "limit": 20}))
            task_detail = _structured(await client.call_tool("get_memory", {"memory_id": task_page["items"][0]["memory_id"]}))
            user_fact = next(fact for fact in task_detail["memory"]["facts"] if fact["role"] == "user")
            preference = _structured(
                await client.call_tool(
                    "remember",
                    {
                        "memory_type": "user_preference",
                        "title": "User output preference",
                        "content": user_fact["value"],
                        "subject_key": "output",
                        "source_fact_ids": [user_fact["fact_id"]],
                    },
                )
            )
            assert preference["memory_type"] == "user_preference"

            malformed = await client.call_tool("list_memories", {"cursor": "not-a-cursor"})
            assert malformed.isError
            assert '"code":"cursor_invalid"' in malformed.content[0].text
            unknown = await client.call_tool("get_memory", {"memory_id": "mem_" + "f" * 64})
            assert unknown.isError
            assert '"code":"memory_not_found"' in unknown.content[0].text
            extra = await client.call_tool("doctor", {"unknown": True})
            assert extra.isError

            concurrent: list[Any] = []

            async def read() -> None:
                concurrent.append(await client.call_tool("doctor", {}))

            async def write() -> None:
                concurrent.append(
                    await client.call_tool(
                        "remember",
                        {
                            "memory_type": "repository_knowledge",
                            "title": "Concurrent write",
                            "content": "One write may overlap independent reads.",
                            "subject_key": "concurrency",
                        },
                    )
                )

            async with anyio.create_task_group() as group:
                group.start_soon(read)
                group.start_soon(read)
                group.start_soon(write)
            assert len(concurrent) == 3
            assert all(not result.isError for result in concurrent)

    anyio.run(exercise)


def test_mcp_schema_snapshot_matches_server(tmp_path: Path) -> None:
    repository, _runtime = _repository(tmp_path)

    async def compare() -> None:
        assert await schema_snapshot(_server(repository)) == json.loads(SCHEMA.read_text())

    anyio.run(compare)


def test_mcp_missing_provider_is_typed(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    initialize_repository(start=repository, root=tmp_path / "runtime", repo_key="acme/widgets", retrieval_profile="dashscope")
    server = build_server(create_application, working_directory=repository)

    async def exercise() -> None:
        async with create_connected_server_and_client_session(server) as client:
            remembered = _structured(
                await client.call_tool(
                    "remember",
                    {
                        "memory_type": "repository_knowledge",
                        "title": "Provider test",
                        "content": "This memory requires index projection.",
                        "subject_key": "provider",
                    },
                )
            )
            assert remembered["memory_id"]
            recalled = await client.call_tool("recall", {"task": "provider test"})
            assert recalled.isError
            assert '"code":"provider_not_configured"' in recalled.content[0].text

    anyio.run(exercise)


def test_packaged_stdio_doctor_and_failure_are_protocol_clean(tmp_path: Path) -> None:
    repository, _runtime = _repository(tmp_path)
    executable = ROOT / ".venv" / "bin" / "codecairn-mcp"
    error_path = tmp_path / "mcp-stderr.log"

    async def smoke(errors: Any) -> None:
        parameters = StdioServerParameters(
            command=str(executable),
            cwd=repository,
            env={key: value for key, value in os.environ.items() if not key.startswith(("COV_CORE_", "COVERAGE_"))},
        )
        async with stdio_client(parameters, errlog=errors) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            doctor = _structured(await client.call_tool("doctor", {}))
            assert doctor["repo_key"] == "acme/widgets"
            failure = await client.call_tool("get_memory", {"memory_id": "mem_" + "f" * 64})
            assert failure.isError

    with error_path.open("w+") as errors:
        anyio.run(smoke, errors)
    assert "Traceback" not in error_path.read_text()
