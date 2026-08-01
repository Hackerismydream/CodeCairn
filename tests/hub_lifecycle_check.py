from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from codecairn.bootstrap import create_application
from codecairn.configuration import initialize_repository
from codecairn.memory.config import RetrievalConfig
from codecairn.service.application import RememberRequest
from scripts.run_hub import REPOSITORY_ROOT, available_port


class _SetupEmbedder:
    """Build a provider-compatible index without making a network request."""

    def __init__(self, config: RetrievalConfig) -> None:
        self.dimension = config.dimension
        self.index_identity = config.index_identity
        self.relevance_threshold = config.relevance_threshold
        self.model_id = config.model
        self.source_id = "hub-lifecycle-setup"
        self.revision = config.revision

    def embed_query(self, _text: str) -> tuple[float, ...]:
        return (0.0,) * self.dimension

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((0.0,) * self.dimension for _text in texts)


class _SetupReranker:
    model_id = "hub-lifecycle-setup"

    def rerank(self, _query: str, documents: tuple[tuple[str, str, float], ...]) -> tuple[tuple[str, float], ...]:
        return tuple((memory_id, score) for memory_id, _text, score in documents)


def test_production_launcher_closes_both_loopback_services_on_sigterm(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    runtime_root = tmp_path / "runtime"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    initialize_repository(
        start=repository,
        root=runtime_root,
        repo_key="local/hub-lifecycle",
        retrieval_profile="dashscope",
        semantic_profile="none",
        environment={},
    )
    retrieval = RetrievalConfig.default("dashscope")
    seed = create_application(
        runtime_root, repo_key="local/hub-lifecycle", retrieval_adapters=(_SetupEmbedder(retrieval), _SetupReranker())
    )
    memory = seed.remember_direct(
        RememberRequest(
            repo_key="local/hub-lifecycle",
            memory_type="repository_knowledge",
            title="前台 Hub 生命周期",
            content="关闭前台 launcher 必须同时关闭 Web 与 API。",
            subject_key="hub-lifecycle",
        )
    )
    seed.sync_index(worker_id="hub-lifecycle-setup")
    api_port = available_port(0)
    web_port = available_port(0)
    environment = {key: value for key, value in os.environ.items() if key not in {"CODECAIRN_EMBEDDING_API_KEY", "DASHSCOPE_API_KEY"}}
    launcher = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_hub.py",
            "--repository",
            str(repository),
            "--production",
            "--api-port",
            str(api_port),
            "--web-port",
            str(web_port),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_for_port(api_port, launcher)
        _wait_for_port(web_port, launcher)
        with urllib.request.urlopen(f"http://127.0.0.1:{web_port}/api/hub-read/v1/system", timeout=5) as response:
            payload = json.load(response)
            assert response.status == 200
            assert payload["repo_key"] == "local/hub-lifecycle"
            assert payload["status"] == "ok"
            assert payload["providers"]["retrieval_state"] == "configured"
            assert payload["recall_readiness"]["state"] == "missing_key"

        with urllib.request.urlopen(f"http://127.0.0.1:{web_port}/api/hub-read/v1/memories", timeout=5) as response:
            payload = json.load(response)
            assert response.status == 200
            assert payload["page"]["items"][0]["memory_id"] == memory.memory_id

        recall = urllib.request.Request(
            f"http://127.0.0.1:{web_port}/api/hub-read/v1/recall",
            data=json.dumps({"query": memory.title}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(recall, timeout=5)
        except urllib.error.HTTPError as error:
            assert error.status == 503
            assert json.load(error)["error"]["code"] == "provider_not_configured"
        else:
            raise AssertionError("Recall without its configured network credential must fail closed")

        rebinding = http.client.HTTPConnection("127.0.0.1", web_port, timeout=5)
        rebinding.request(
            "GET",
            "/api/hub-read/v1/system",
            headers={"Host": f"attacker.example:{web_port}", "Origin": f"http://attacker.example:{web_port}"},
        )
        rejected = rebinding.getresponse()
        assert rejected.status == 403
        assert json.load(rejected)["error"]["code"] == "untrusted_browser_origin"
        rebinding.close()

        launcher.send_signal(signal.SIGTERM)

        assert launcher.wait(timeout=10) == 128 + signal.SIGTERM
        _assert_port_closed(api_port)
        _assert_port_closed(web_port)
    finally:
        if launcher.poll() is None:
            launcher.send_signal(signal.SIGTERM)
            try:
                launcher.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(launcher.pid, signal.SIGKILL)
                launcher.wait()


def test_development_launcher_serves_the_same_origin_hub_interface(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    runtime_root = tmp_path / "runtime"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    initialize_repository(
        start=repository,
        root=runtime_root,
        repo_key="local/hub-development-lifecycle",
        retrieval_profile="dashscope",
        semantic_profile="none",
        environment={},
    )
    api_port = available_port(0)
    web_port = available_port(0)
    ready_file = tmp_path / "hub-ready.json"
    environment = {key: value for key, value in os.environ.items() if key not in {"CODECAIRN_EMBEDDING_API_KEY", "DASHSCOPE_API_KEY"}}
    launcher = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_hub.py",
            "--repository",
            str(repository),
            "--api-port",
            str(api_port),
            "--web-port",
            str(web_port),
            "--ready-file",
            str(ready_file),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_for_ready_file(ready_file, launcher)
        with urllib.request.urlopen(f"http://127.0.0.1:{web_port}/api/hub-read/v1/system", timeout=5) as response:
            payload = json.load(response)
            assert response.status == 200
            assert payload["repo_key"] == "local/hub-development-lifecycle"

        launcher.send_signal(signal.SIGTERM)

        assert launcher.wait(timeout=10) == 128 + signal.SIGTERM
        _assert_port_closed(api_port)
        _assert_port_closed(web_port)
    finally:
        if launcher.poll() is None:
            launcher.send_signal(signal.SIGTERM)
            try:
                launcher.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(launcher.pid, signal.SIGKILL)
                launcher.wait()


def _wait_for_port(port: int, launcher: subprocess.Popen[bytes], *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if launcher.poll() is not None:
            raise AssertionError(f"Hub launcher exited early with {launcher.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"Hub did not listen on 127.0.0.1:{port}")


def _wait_for_ready_file(path: Path, launcher: subprocess.Popen[bytes], *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if launcher.poll() is not None:
            raise AssertionError(f"Hub launcher exited early with {launcher.returncode}")
        if path.is_file():
            return
        time.sleep(0.05)
    raise AssertionError(f"Hub launcher did not publish {path}")


def _assert_port_closed(port: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                time.sleep(0.05)
                continue
        except OSError:
            return
    raise AssertionError(f"Hub still accepts connections on 127.0.0.1:{port}")
