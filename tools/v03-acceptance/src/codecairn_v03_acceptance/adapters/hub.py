"""Same-origin Hub Read adapter with an allowlisted evidence projection."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import signal
import stat
import subprocess
import tempfile
import time
import urllib.parse
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codecairn.evaluation.artifacts import file_sha256

SEMANTIC_SHA256_FIELD = "semantic_sha256"


class HubAdapterError(RuntimeError):
    """A stable Hub process, transport, or contract failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class OperationReceipt:
    operation: str
    http_status: int
    request_id: str
    body_sha256: str
    projection: dict[str, object]
    semantic_sha256: str = ""


@dataclass(frozen=True, slots=True)
class HubSnapshot:
    system: OperationReceipt
    memories: OperationReceipt
    lifecycle_memories: OperationReceipt
    recall: OperationReceipt
    machine_observation: dict[str, object]


@dataclass(frozen=True, slots=True)
class SourceCheckoutHubSession:
    client: HubReadClient
    launcher_pid: int
    child_process_groups: dict[str, int]


class HubReadClient:
    """Read the three product operations through the browser's same-origin seam."""

    def __init__(self, origin: str, *, timeout_seconds: float = 30.0) -> None:
        parsed = urllib.parse.urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Hub origin must be an uncredentialed 127.0.0.1 HTTP origin")
        self._origin = f"http://127.0.0.1:{parsed.port}"
        self._port = parsed.port
        self._timeout_seconds = timeout_seconds

    @property
    def origin(self) -> str:
        return self._origin

    def system(self) -> OperationReceipt:
        payload, status, request_id, digest = self._request("GET", "/api/hub-read/v1/system")
        return _operation_receipt("system", payload, status, request_id, digest, _project_system(payload))

    def memories(self, *, selected_memory_id: str) -> OperationReceipt:
        query = urllib.parse.urlencode({"selected_memory_id": selected_memory_id})
        payload, status, request_id, digest = self._request("GET", f"/api/hub-read/v1/memories?{query}")
        return _operation_receipt("memories", payload, status, request_id, digest, _project_memories(payload))

    def recall(self, *, query: str) -> OperationReceipt:
        payload, status, request_id, digest = self._request(
            "POST", "/api/hub-read/v1/recall", body={"query": query, "include_superseded": False}
        )
        return _operation_receipt("recall", payload, status, request_id, digest, _project_recall(payload))

    def snapshot(self, *, query: str, selected_memory_id: str, lifecycle_memory_id: str) -> HubSnapshot:
        system = self.system()
        memories = self.memories(selected_memory_id=selected_memory_id)
        lifecycle_memories = self.memories(selected_memory_id=lifecycle_memory_id)
        recall = self.recall(query=query)
        system_projection = system.projection
        memories_projection = memories.projection
        lifecycle_projection = lifecycle_memories.projection
        recall_projection = recall.projection
        items = cast(list[dict[str, str]], memories_projection["items"])
        lifecycle_items = cast(list[dict[str, str]], lifecycle_projection["items"])
        if lifecycle_projection["repo_key"] != memories_projection["repo_key"] or lifecycle_items != items:
            raise HubAdapterError("hub_snapshot_drifted", "Hub memory views changed while the evidence snapshot was collected")
        statuses = {item["memory_id"]: item["status"] for item in items}
        machine_observation: dict[str, object] = {
            "adapter": "http",
            "repository_key": memories_projection["repo_key"],
            "system_repository_key": system_projection["repo_key"],
            "recall_repository_key": recall_projection["repo_key"],
            "lifecycle_repository_key": lifecycle_projection["repo_key"],
            "memories_memory_ids": [item["memory_id"] for item in items],
            "selected_memory_id": memories_projection["selected_memory_id"],
            "selected_evidence_fact_ids": memories_projection["selected_evidence_fact_ids"],
            "selected_evidence_references": memories_projection["selected_evidence_references"],
            "recall_memory_ids": recall_projection["rendered_memory_ids"],
            "recall_ranked_memory_ids": recall_projection["ranked_memory_ids"],
            "recall_admission": recall_projection["admission"],
            "recall_omissions": recall_projection["omissions"],
            "recall_context_sha256": recall_projection["context_sha256"],
            "system_status": system_projection["status"],
            "recall_readiness": system_projection["recall_readiness"],
            "statuses": statuses,
            "supersessions": lifecycle_projection["supersessions"],
        }
        return HubSnapshot(
            system=system,
            memories=memories,
            lifecycle_memories=lifecycle_memories,
            recall=recall,
            machine_observation=machine_observation,
        )

    def _request(self, method: str, path: str, *, body: dict[str, object] | None = None) -> tuple[dict[str, object], int, str, str]:
        encoded = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        headers = {"Accept": "application/json", "Connection": "close", "Origin": self._origin, "Sec-Fetch-Site": "same-origin"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=self._timeout_seconds)
        try:
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            raw = response.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise HubAdapterError("hub_contract_incompatible", "Hub response exceeds the evidence limit")
            request_id = response.getheader("x-codecairn-request-id", "")
            content_type = response.getheader("content-type", "").split(";", 1)[0].strip().lower()
            try:
                decoded = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise HubAdapterError("hub_contract_incompatible", "Hub returned non-JSON evidence") from error
            if content_type != "application/json" or not isinstance(decoded, dict):
                raise HubAdapterError("hub_contract_incompatible", "Hub response contract is not JSON object")
            payload = cast(dict[str, object], decoded)
            if response.status != 200:
                error_payload = payload.get("error")
                code = (
                    str(cast(dict[str, object], error_payload).get("code")) if isinstance(error_payload, dict) else "hub_http_failure"
                )
                raise HubAdapterError(code, f"Hub returned HTTP {response.status}")
            if not request_id:
                raise HubAdapterError("hub_contract_incompatible", "Hub response is missing its request identity")
            return payload, response.status, request_id, hashlib.sha256(raw).hexdigest()
        finally:
            connection.close()


def hub_web_bundle_identity(checkout: Path) -> dict[str, object]:
    """Hash the ignored production bundle that the source Hub actually serves."""
    bundle = checkout.resolve() / "apps" / "hub-web" / "dist"
    if bundle.is_symlink() or not bundle.is_dir():
        raise HubAdapterError("hub_bundle_invalid", "Hub production bundle is missing")
    files: list[dict[str, object]] = []
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise HubAdapterError("hub_bundle_invalid", "Hub production bundle contains a symlink")
        if path.is_file():
            files.append({"path": path.relative_to(bundle).as_posix(), "bytes": path.stat().st_size, "sha256": file_sha256(path)})
    if not files:
        raise HubAdapterError("hub_bundle_invalid", "Hub production bundle is empty")
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {
        "file_count": len(files),
        "bytes": sum(cast(int, item["bytes"]) for item in files),
        "tree_sha256": hashlib.sha256(encoded).hexdigest(),
    }


@contextmanager
def source_checkout_hub(
    *,
    checkout: Path,
    repository: Path,
    python_executable: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> Iterator[SourceCheckoutHubSession]:
    """Launch one source-checkout Hub and expose only its same-origin read client."""
    checkout = checkout.resolve()
    repository = repository.resolve()
    selected_executable = python_executable or checkout / ".venv" / "bin" / "python"
    if not selected_executable.is_absolute():
        raise HubAdapterError("hub_preflight_failed", "Hub interpreter must be an explicit absolute path")
    # Keep the lexical venv path. Resolving a venv's Python symlink to the base
    # interpreter discards pyvenv.cfg discovery and therefore the installed Hub
    # environment.
    executable = selected_executable.absolute()
    launcher = checkout / "scripts" / "run_hub.py"
    if (
        not checkout.is_dir()
        or not repository.is_dir()
        or not launcher.is_file()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise HubAdapterError("hub_preflight_failed", "Hub checkout, repository, launcher, or interpreter is missing")
    source_environment = os.environ if environment is None else environment
    allowed = {
        "CODECAIRN_EMBEDDING_API_KEY",
        "DASHSCOPE_API_KEY",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
    child_environment = {key: value for key, value in source_environment.items() if key in allowed}
    child_environment.update({"PYTHONPATH": "", "NO_PROXY": "127.0.0.1,localhost"})
    with tempfile.TemporaryDirectory(prefix="codecairn-v03-hub-") as temporary:
        ready_path = Path(temporary) / "ready.json"
        process = subprocess.Popen(
            [
                str(executable),
                "-I",
                str(launcher),
                "--repository",
                str(repository),
                "--production",
                "--api-port",
                "0",
                "--web-port",
                "0",
                "--ready-file",
                str(ready_path),
            ],
            cwd=checkout,
            env=child_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        receipt: dict[str, object] | None = None
        try:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if ready_path.is_file():
                    receipt = _ready_receipt(ready_path, launcher_pid=process.pid)
                    break
                if process.poll() is not None:
                    raise HubAdapterError("hub_process_exited", f"Hub launcher exited with {process.returncode}")
                time.sleep(0.05)
            if receipt is None:
                raise HubAdapterError("hub_start_timeout", "Hub launcher did not publish readiness")
            groups = cast(dict[str, int], receipt["child_process_groups"])
            yield SourceCheckoutHubSession(
                client=HubReadClient(cast(str, receipt["web_origin"])), launcher_pid=process.pid, child_process_groups=groups
            )
        finally:
            _stop_hub(process, receipt)


def _ready_receipt(path: Path, *, launcher_pid: int) -> dict[str, object]:
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise HubAdapterError("hub_contract_incompatible", "Hub ready receipt is not private")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HubAdapterError("hub_contract_incompatible", "Hub ready receipt is unreadable") from error
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "contract",
        "web_origin",
        "api_port",
        "web_port",
        "launcher_pid",
        "child_process_groups",
    }:
        raise HubAdapterError("hub_contract_incompatible", "Hub ready receipt fields are invalid")
    receipt = cast(dict[str, object], value)
    groups = receipt["child_process_groups"]
    if (
        receipt["schema_version"] != 1
        or receipt["contract"] != "codecairn.hub.launcher-ready.v1"
        or receipt["launcher_pid"] != launcher_pid
        or not isinstance(receipt["api_port"], int)
        or isinstance(receipt["api_port"], bool)
        or not isinstance(receipt["web_port"], int)
        or isinstance(receipt["web_port"], bool)
        or not isinstance(receipt["web_origin"], str)
        or not isinstance(groups, dict)
        or set(groups) != {"api", "web"}
        or any(not isinstance(group, int) or isinstance(group, bool) or group <= 0 for group in groups.values())
    ):
        raise HubAdapterError("hub_contract_incompatible", "Hub ready receipt values are invalid")
    parsed = urllib.parse.urlsplit(receipt["web_origin"])
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != receipt["web_port"]:
        raise HubAdapterError("hub_contract_incompatible", "Hub ready origin does not match its port")
    return receipt


def _stop_hub(process: subprocess.Popen[bytes], receipt: dict[str, object] | None) -> None:
    if process.poll() is None:
        with suppress(ProcessLookupError):
            process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            groups = set(cast(dict[str, int], receipt["child_process_groups"]).values()) if receipt is not None else set()
            groups.add(process.pid)
            for group in groups:
                with suppress(ProcessLookupError):
                    os.killpg(group, signal.SIGKILL)
            process.wait(timeout=5)
    if process.returncode not in {128 + signal.SIGTERM, -signal.SIGTERM}:
        raise HubAdapterError("hub_cleanup_failed", f"Hub launcher exited with {process.returncode}")


def _project_system(value: dict[str, object]) -> dict[str, object]:
    if value.get("schema_version") != 1 or not isinstance(value.get("repo_key"), str) or not isinstance(value.get("status"), str):
        raise HubAdapterError("hub_contract_incompatible", "System response is missing required fields")
    counts = _object(value.get("counts"), field="system counts")
    readiness = _object(value.get("recall_readiness"), field="recall readiness")
    if not isinstance(readiness.get("state"), str) or not isinstance(readiness.get("live_checked"), bool):
        raise HubAdapterError("hub_contract_incompatible", "Recall readiness is invalid")
    return {
        "schema_version": 1,
        "repo_key": value["repo_key"],
        "status": value["status"],
        "counts": {key: item for key, item in counts.items() if isinstance(item, int) and not isinstance(item, bool)},
        "recall_readiness": {"state": readiness["state"], "live_checked": readiness["live_checked"]},
    }


def _project_memories(value: dict[str, object]) -> dict[str, object]:
    if value.get("schema_version") != 1 or not isinstance(value.get("repo_key"), str):
        raise HubAdapterError("hub_contract_incompatible", "Memories response is missing required fields")
    page = _object(value.get("page"), field="memory page")
    raw_items = page.get("items")
    if not isinstance(raw_items, list):
        raise HubAdapterError("hub_contract_incompatible", "Memory page items are invalid")
    items: list[dict[str, str]] = []
    for raw_item in raw_items:
        item = _object(raw_item, field="memory item")
        if any(not isinstance(item.get(name), str) for name in ("memory_id", "memory_type", "status")):
            raise HubAdapterError("hub_contract_incompatible", "Memory item identity is invalid")
        items.append(
            {
                "memory_id": cast(str, item["memory_id"]),
                "memory_type": cast(str, item["memory_type"]),
                "status": cast(str, item["status"]),
            }
        )
    selected = _object(value.get("selected"), field="selected memory")
    detail = _object(selected.get("detail"), field="selected memory detail")
    memory = _object(detail.get("memory"), field="selected Coding Memory")
    selected_memory_id = memory.get("memory_id")
    if not isinstance(selected_memory_id, str):
        raise HubAdapterError("hub_contract_incompatible", "Selected memory identity is invalid")
    evidence = memory.get("evidence")
    if not isinstance(evidence, list):
        raise HubAdapterError("hub_contract_incompatible", "Selected memory evidence is invalid")
    evidence_references: list[dict[str, str]] = []
    for raw_reference in evidence:
        reference = _object(raw_reference, field="selected memory Evidence Reference")
        if any(not isinstance(reference.get(name), str) or not reference[name] for name in ("fact_id", "provider", "session_id")):
            raise HubAdapterError("hub_contract_incompatible", "Selected memory Evidence Reference is invalid")
        evidence_references.append(
            {
                "fact_id": cast(str, reference["fact_id"]),
                "provider": cast(str, reference["provider"]),
                "session_id": cast(str, reference["session_id"]),
            }
        )
    evidence_fact_ids = [reference["fact_id"] for reference in evidence_references]
    history = _object(selected.get("history"), field="selected memory history")
    raw_evolutions = history.get("evolutions")
    if not isinstance(raw_evolutions, list):
        raise HubAdapterError("hub_contract_incompatible", "Selected memory evolution history is invalid")
    supersessions: list[dict[str, str]] = []
    for raw_evolution in raw_evolutions:
        evolution = _object(raw_evolution, field="memory evolution")
        predecessor_id = evolution.get("predecessor_id")
        successor_id = evolution.get("successor_id")
        if not isinstance(predecessor_id, str) or not isinstance(successor_id, str):
            raise HubAdapterError("hub_contract_incompatible", "Memory evolution identity is invalid")
        supersessions.append({"predecessor_id": predecessor_id, "successor_id": successor_id})
    return {
        "schema_version": 1,
        "repo_key": value["repo_key"],
        "items": items,
        "selected_memory_id": selected_memory_id,
        "selected_evidence_fact_ids": evidence_fact_ids,
        "selected_evidence_references": evidence_references,
        "supersessions": supersessions,
    }


def _project_recall(value: dict[str, object]) -> dict[str, object]:
    if value.get("schema_version") != 1:
        raise HubAdapterError("hub_contract_incompatible", "Recall response schema is invalid")
    result = _object(value.get("result"), field="recall result")
    sidecar = _object(result.get("sidecar"), field="recall sidecar")
    admission = _object(sidecar.get("admission_trace"), field="recall admission")
    context = _object(sidecar.get("context_trace"), field="recall context")
    ranked = sidecar.get("ranked")
    omissions = sidecar.get("omissions")
    rendered = context.get("rendered_memory_ids")
    if (
        not isinstance(sidecar.get("repo_key"), str)
        or not isinstance(admission.get("outcome"), str)
        or not isinstance(admission.get("reason"), str)
        or not isinstance(ranked, list)
        or not isinstance(omissions, list)
        or not isinstance(rendered, list)
        or any(not isinstance(memory_id, str) for memory_id in rendered)
        or not isinstance(result.get("markdown"), str)
    ):
        raise HubAdapterError("hub_contract_incompatible", "Recall evidence is invalid")
    ranked_memory_ids = [
        item["memory_id"]
        for raw_item in ranked
        if isinstance(raw_item, dict) and isinstance((item := cast(dict[str, object], raw_item)).get("memory_id"), str)
    ]
    omission_projection = [
        {"memory_id": item["memory_id"], "reason": item["reason"]}
        for raw_item in omissions
        if isinstance(raw_item, dict)
        and isinstance((item := cast(dict[str, object], raw_item)).get("memory_id"), str)
        and isinstance(item.get("reason"), str)
    ]
    markdown = cast(str, result["markdown"])
    return {
        "schema_version": 1,
        "repo_key": sidecar["repo_key"],
        "admission": {"outcome": admission["outcome"], "reason": admission["reason"]},
        "ranked_memory_ids": ranked_memory_ids,
        "rendered_memory_ids": cast(list[str], rendered),
        "omissions": omission_projection,
        "context_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
    }


def _operation_receipt(
    operation: str, payload: dict[str, object], http_status: int, request_id: str, body_sha256: str, projection: dict[str, object]
) -> OperationReceipt:
    semantic_sha256 = _semantic_sha256(operation, payload)
    if SEMANTIC_SHA256_FIELD in projection:
        raise HubAdapterError("hub_contract_incompatible", "Hub projection collides with its semantic digest")
    return OperationReceipt(
        operation=operation,
        http_status=http_status,
        request_id=request_id,
        body_sha256=body_sha256,
        projection={**projection, SEMANTIC_SHA256_FIELD: semantic_sha256},
        semantic_sha256=semantic_sha256,
    )


def _semantic_sha256(operation: str, payload: dict[str, object]) -> str:
    omitted_paths: set[tuple[str, ...]]
    if operation == "system":
        omitted_paths = {("observed_at_ms",)}
    elif operation == "recall":
        omitted_paths = {("result", "sidecar", "latency_ms")}
    else:
        omitted_paths = set()
    semantic_payload = _without_paths(payload, path=(), omitted_paths=omitted_paths)
    try:
        canonical = json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as error:
        raise HubAdapterError("hub_contract_incompatible", "Hub success JSON cannot be canonically hashed") from error
    return hashlib.sha256(canonical).hexdigest()


def _without_paths(value: object, *, path: tuple[str, ...], omitted_paths: set[tuple[str, ...]]) -> object:
    if isinstance(value, dict):
        return {
            key: _without_paths(item, path=(*path, key), omitted_paths=omitted_paths)
            for key, item in value.items()
            if (*path, key) not in omitted_paths
        }
    if isinstance(value, list):
        return [_without_paths(item, path=path, omitted_paths=omitted_paths) for item in value]
    return value


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise HubAdapterError("hub_contract_incompatible", f"{field} must be an object")
    return cast(dict[str, object], value)
