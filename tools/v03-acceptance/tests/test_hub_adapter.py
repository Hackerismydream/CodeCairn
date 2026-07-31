from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from codecairn_v03_acceptance.adapters.hub import HubReadClient, _semantic_sha256, source_checkout_hub


def test_semantic_digest_excludes_only_the_declared_request_time_fields() -> None:
    system = {"status": "ok", "observed_at_ms": 100, "nested": {"observed_at_ms": 1}}
    assert _semantic_sha256("system", system) == _semantic_sha256("system", {**system, "observed_at_ms": 200})
    assert _semantic_sha256("system", system) != _semantic_sha256("system", {**system, "nested": {"observed_at_ms": 2}})

    recall = {"result": {"markdown": "first", "sidecar": {"latency_ms": 10, "ranked": []}}}
    changed_latency = {"result": {"markdown": "first", "sidecar": {"latency_ms": 20, "ranked": []}}}
    changed_content = {"result": {"markdown": "second", "sidecar": {"latency_ms": 10, "ranked": []}}}
    assert _semantic_sha256("recall", recall) == _semantic_sha256("recall", changed_latency)
    assert _semantic_sha256("recall", recall) != _semantic_sha256("recall", changed_content)


def test_hub_client_uses_same_origin_routes_and_returns_only_sanitized_evidence() -> None:
    remembered = "mem_" + "1" * 64
    predecessor = "mem_" + "2" * 64
    successor = "mem_" + "3" * 64
    requests: list[tuple[str, str, str | None]] = []
    volatile_sequence = iter((101, 11, 202, 22))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append((self.command, self.path, self.headers.get("Origin")))
            if self.path == "/api/hub-read/v1/system":
                self._respond(
                    {
                        "schema_version": 1,
                        "repo_key": "local/v03-acceptance",
                        "status": "ok",
                        "counts": {"memories": 3},
                        "recall_readiness": {"state": "configuration_ready", "live_checked": False},
                        "observed_at_ms": next(volatile_sequence),
                    }
                )
                return
            if self.path.startswith("/api/hub-read/v1/memories"):
                selected_memory_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["selected_memory_id"][0]
                self._respond(
                    {
                        "schema_version": 1,
                        "repo_key": "local/v03-acceptance",
                        "page": {
                            "schema_version": 1,
                            "repo_key": "local/v03-acceptance",
                            "next_cursor": None,
                            "items": [
                                {
                                    "memory_id": remembered,
                                    "memory_type": "task_experience",
                                    "status": "active",
                                    "title": "PRIVATE TITLE",
                                    "created_at_ms": 1,
                                },
                                {
                                    "memory_id": predecessor,
                                    "memory_type": "repository_knowledge",
                                    "status": "superseded",
                                    "title": "PRIVATE OLD CONTENT",
                                    "created_at_ms": 2,
                                },
                                {
                                    "memory_id": successor,
                                    "memory_type": "repository_knowledge",
                                    "status": "active",
                                    "title": "PRIVATE NEW CONTENT",
                                    "created_at_ms": 3,
                                },
                            ],
                        },
                        "selected": {
                            "detail": {
                                "status": "active",
                                "resource_uri": f"codecairn://memory/{selected_memory_id}",
                                "memory": {
                                    "memory_id": selected_memory_id,
                                    "content": "SECRET-CANARY",
                                    "evidence": (
                                        [{"fact_id": "fact_" + "a" * 64, "provider": "pico", "session_id": "cli:v03-learn-001"}]
                                        if selected_memory_id == remembered
                                        else []
                                    ),
                                },
                            },
                            "history": {
                                "statuses": [[predecessor, "superseded"], [successor, "active"]],
                                "evolutions": (
                                    [{"predecessor_id": predecessor, "successor_id": successor}]
                                    if selected_memory_id == successor
                                    else []
                                ),
                                "memories": [],
                            },
                        },
                    }
                )
                return
            self.send_error(404)

        def do_POST(self) -> None:
            requests.append((self.command, self.path, self.headers.get("Origin")))
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            assert request["query"] == "默认重试次数为什么变化？"
            self._respond(
                {
                    "schema_version": 1,
                    "result": {
                        "markdown": "PRIVATE COMPILED CONTEXT",
                        "sidecar": {
                            "repo_key": "local/v03-acceptance",
                            "admission_trace": {"outcome": "admitted", "reason": "relevant_candidate"},
                            "context_trace": {"rendered_memory_ids": [remembered]},
                            "ranked": [{"memory_id": remembered}],
                            "omissions": [{"memory_id": predecessor, "reason": "lifecycle"}],
                            "latency_ms": next(volatile_sequence),
                        },
                    },
                }
            )

        def _respond(self, value: dict[str, Any]) -> None:
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.send_header("x-codecairn-request-id", "hubreq_test")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        client = HubReadClient(origin)
        snapshot = client.snapshot(query="默认重试次数为什么变化？", selected_memory_id=remembered, lifecycle_memory_id=successor)
        second_snapshot = client.snapshot(
            query="默认重试次数为什么变化？", selected_memory_id=remembered, lifecycle_memory_id=successor
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert requests == [
        ("GET", "/api/hub-read/v1/system", origin),
        ("GET", f"/api/hub-read/v1/memories?selected_memory_id={remembered}", origin),
        ("GET", f"/api/hub-read/v1/memories?selected_memory_id={successor}", origin),
        ("POST", "/api/hub-read/v1/recall", origin),
        ("GET", "/api/hub-read/v1/system", origin),
        ("GET", f"/api/hub-read/v1/memories?selected_memory_id={remembered}", origin),
        ("GET", f"/api/hub-read/v1/memories?selected_memory_id={successor}", origin),
        ("POST", "/api/hub-read/v1/recall", origin),
    ]
    assert snapshot.machine_observation == {
        "adapter": "http",
        "repository_key": "local/v03-acceptance",
        "system_repository_key": "local/v03-acceptance",
        "recall_repository_key": "local/v03-acceptance",
        "lifecycle_repository_key": "local/v03-acceptance",
        "memories_memory_ids": [remembered, predecessor, successor],
        "selected_memory_id": remembered,
        "selected_evidence_fact_ids": ["fact_" + "a" * 64],
        "selected_evidence_references": [{"fact_id": "fact_" + "a" * 64, "provider": "pico", "session_id": "cli:v03-learn-001"}],
        "recall_memory_ids": [remembered],
        "recall_ranked_memory_ids": [remembered],
        "recall_admission": {"outcome": "admitted", "reason": "relevant_candidate"},
        "recall_omissions": [{"memory_id": predecessor, "reason": "lifecycle"}],
        "recall_context_sha256": snapshot.recall.projection["context_sha256"],
        "system_status": "ok",
        "recall_readiness": {"state": "configuration_ready", "live_checked": False},
        "statuses": {remembered: "active", predecessor: "superseded", successor: "active"},
        "supersessions": [{"predecessor_id": predecessor, "successor_id": successor}],
    }
    serialized = json.dumps(asdict(snapshot), sort_keys=True)
    assert "SECRET-CANARY" not in serialized
    assert "PRIVATE" not in serialized
    assert all(
        receipt.request_id == "hubreq_test"
        for receipt in (snapshot.system, snapshot.memories, snapshot.lifecycle_memories, snapshot.recall)
    )
    assert snapshot.system.body_sha256 != second_snapshot.system.body_sha256
    assert snapshot.recall.body_sha256 != second_snapshot.recall.body_sha256
    assert snapshot.system.semantic_sha256 == second_snapshot.system.semantic_sha256
    assert snapshot.recall.semantic_sha256 == second_snapshot.recall.semantic_sha256
    for receipt in (snapshot.system, snapshot.memories, snapshot.lifecycle_memories, snapshot.recall):
        assert receipt.projection["semantic_sha256"] == receipt.semantic_sha256
        assert len(receipt.semantic_sha256) == 64


def test_source_checkout_host_consumes_ready_receipt_and_reaps_the_launcher(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    repository = tmp_path / "repository"
    script = checkout / "scripts" / "run_hub.py"
    script.parent.mkdir(parents=True)
    repository.mkdir()
    script.write_text(
        """
import argparse
import json
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--repository")
parser.add_argument("--production", action="store_true")
parser.add_argument("--api-port")
parser.add_argument("--web-port")
parser.add_argument("--ready-file")
args = parser.parse_args()
with open(os.path.join(args.repository, "expected-prefix.txt"), encoding="utf-8") as handle:
    expected_prefix = handle.read()
if sys.prefix != expected_prefix:
    raise SystemExit(93)
if not sys.flags.isolated or os.environ.get("HUB_SITE_CANARY"):
    raise SystemExit(94)
running = True

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({
            "schema_version": 1,
            "repo_key": "local/fake-host",
            "status": "ok",
            "counts": {},
            "recall_readiness": {"state": "configuration_ready", "live_checked": False},
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("x-codecairn-request-id", "hubreq_fake_host")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.timeout = 0.1
port = server.server_address[1]
receipt = {
    "schema_version": 1,
    "contract": "codecairn.hub.launcher-ready.v1",
    "web_origin": f"http://127.0.0.1:{port}",
    "api_port": port + 1,
    "web_port": port,
    "launcher_pid": os.getpid(),
    "child_process_groups": {"api": os.getpgrp(), "web": os.getpgrp()},
}
with open(args.ready_file, "x", encoding="utf-8") as handle:
    json.dump(receipt, handle)
os.chmod(args.ready_file, 0o600)

def stop(_signum, _frame):
    global running
    running = False

signal.signal(signal.SIGTERM, stop)
while running:
    server.handle_request()
server.server_close()
raise SystemExit(128 + signal.SIGTERM)
""",
        encoding="utf-8",
    )
    venv = tmp_path / "hub-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(venv)],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    home = tmp_path / "home"
    home.mkdir()
    probe_environment = {**os.environ, "HOME": str(home)}
    user_site = Path(
        subprocess.run(
            [venv / "bin" / "python", "-c", "import site; print(site.getusersitepackages())"],
            check=True,
            capture_output=True,
            text=True,
            env=probe_environment,
        ).stdout.strip()
    )
    user_site.mkdir(parents=True)
    (user_site / "sitecustomize.py").write_text("import os\nos.environ['HUB_SITE_CANARY'] = 'loaded'\n", encoding="utf-8")
    assert (
        subprocess.run(
            [venv / "bin" / "python", "-c", "import os; print(os.environ.get('HUB_SITE_CANARY', 'missing'))"],
            check=True,
            capture_output=True,
            text=True,
            env=probe_environment,
        ).stdout.strip()
        == "loaded"
    )
    (repository / "expected-prefix.txt").write_text(str(venv), encoding="utf-8")

    with source_checkout_hub(
        checkout=checkout,
        repository=repository,
        python_executable=(venv / "bin" / "python").absolute(),
        environment={"HOME": str(home), "PATH": os.environ["PATH"]},
        timeout_seconds=5,
    ) as session:
        launcher_pid = session.launcher_pid
        receipt = session.client.system()
        assert receipt.projection["repo_key"] == "local/fake-host"

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and _process_exists(launcher_pid):
        time.sleep(0.05)
    assert not _process_exists(launcher_pid)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
