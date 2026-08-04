from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient, JenkinsError, _job_path
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.security import Policy


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "JENKINS_URL": "https://jenkins.test",
        "JENKINS_USERNAME": "admin",
        "JENKINS_TOKEN": "token",
        "JENKINS_MAX_RETRIES": 0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def make_policy(**overrides: object) -> Policy:
    values: dict[str, object] = {
        "read_only": False,
        "allow_job_write": True,
        "allow_build_write": True,
        "allow_node_write": True,
        "allow_admin_request": True,
        "job_patterns": ["*"],
        # These suites exercise destructive paths, so opt in explicitly.
        "allow_job_delete": True,
    }
    values.update(overrides)
    return Policy(**values)  # type: ignore[arg-type]


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    **settings: object,
) -> JenkinsClient:
    return JenkinsClient(
        make_settings(**settings),
        make_policy(),
        AuditLogger(),
        httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_read_and_mutation_methods() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append((request.method, path))
        if path == "/crumbIssuer/api/json":
            return httpx.Response(404)
        if path.endswith("/api/json"):
            if path == "/computer/api/json":
                return httpx.Response(
                    200,
                    json={
                        "computer": [
                            {
                                "displayName": "built-in",
                                "executors": [
                                    {"currentExecutable": {"url": "https://jenkins/job/demo/1/"}}
                                ],
                                "oneOffExecutors": [],
                            }
                        ]
                    },
                )
            if path == "/computer/agent 1/api/json":
                return httpx.Response(
                    200,
                    json={
                        "displayName": "agent 1",
                        "temporarilyOffline": False,
                    },
                )
            return httpx.Response(
                200,
                json={"name": "demo", "jobs": [{"name": "demo"}]},
            )
        if path == "/job/demo/config.xml" and request.method == "GET":
            return httpx.Response(200, text="<project/>")
        if path == "/job/demo/config.xml" and request.method == "POST":
            return httpx.Response(200)
        if path == "/job/demo/buildWithParameters":
            return httpx.Response(
                201,
                headers={"Location": "https://jenkins.test/queue/item/2/"},
            )
        if path == "/job/demo/build":
            return httpx.Response(
                201,
                headers={"Location": "https://jenkins.test/queue/item/2/"},
            )
        if path in {
            "/job/demo/enable",
            "/job/demo/disable",
            "/queue/cancelItem",
            "/computer/agent 1/toggleOffline",
        }:
            return httpx.Response(
                200,
                headers={"Location": "https://jenkins.test/queue/item/2/"},
            )
        if path == "/createItem":
            # Jenkins redirects to the copied job after a successful copy.
            return httpx.Response(302, headers={"Location": "/job/demo-copy/"})
        if path == "/manage/reload":
            return httpx.Response(302, text="reloading", headers={"X-Jenkins": "2"})
        return httpx.Response(404, text=f"unhandled {request.method} {path}")

    client = make_client(handler)

    assert (await client.list_jobs())["jobs"][0]["name"] == "demo"
    assert (await client.list_jobs("folder"))["jobs"][0]["name"] == "demo"
    assert (await client.get_job("demo"))["name"] == "demo"
    assert await client.get_job_config("demo") == "<project/>"
    assert (await client.update_job("demo", "<project/>"))["updated"] == "demo"
    assert (await client.enable_job("demo", True))["enabled"] is True
    assert (await client.enable_job("demo", False))["enabled"] is False
    assert (await client.copy_job("demo", "demo-copy"))["target"] == "demo-copy"
    assert (await client.build("demo", {"ENV": "test"}))["queued"] is True
    assert (await client.build_info("demo", 1))["name"] == "demo"
    assert (await client.queue())["name"] == "demo"
    assert (await client.cancel_queue(5))["cancelled"] == 5
    assert (await client.nodes())["computer"][0]["displayName"] == "built-in"
    assert (await client.node_info("agent 1"))["displayName"] == "agent 1"
    assert (await client.running_builds())[0]["node"] == "built-in"
    toggled = await client.toggle_node("agent 1", True, "maintenance")
    assert toggled["offline"] is True
    assert (await client.scan_multibranch("demo"))["scan_triggered"] == "demo"
    admin = await client.admin_request("POST", "/manage/reload", "{}")
    assert admin["status"] == 302
    assert admin["body"] == "reloading"

    assert ("POST", "/job/demo/buildWithParameters") in requests
    assert ("POST", "/computer/agent 1/toggleOffline") in requests
    await client.close()


@pytest.mark.asyncio
async def test_toggle_node_is_idempotent() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/computer/agent/api/json":
            return httpx.Response(200, json={"temporarilyOffline": True})
        return httpx.Response(404)

    client = make_client(handler)
    result = await client.toggle_node("agent", True)
    assert result == {"node": "agent", "offline": True}
    assert "/computer/agent/toggleOffline" not in calls
    await client.close()


@pytest.mark.asyncio
async def test_request_retries_transient_response(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="starting")
        return httpx.Response(200, json={"ok": True})

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("jenkins_mcp_server.client.asyncio.sleep", no_sleep)
    client = make_client(handler, JENKINS_MAX_RETRIES=1)
    response = await client.request("GET", "/api/json", action="test.retry")
    assert response.json() == {"ok": True}
    assert attempts == 2
    await client.close()


@pytest.mark.asyncio
async def test_request_retries_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("network down", request=request)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("jenkins_mcp_server.client.asyncio.sleep", no_sleep)
    client = make_client(handler, JENKINS_MAX_RETRIES=1)
    with pytest.raises(JenkinsError, match="network down"):
        await client.request("GET", "/api/json", action="test.network")
    assert attempts == 2
    await client.close()


@pytest.mark.asyncio
async def test_request_reports_jenkins_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = make_client(handler)
    with pytest.raises(JenkinsError, match="403: forbidden"):
        await client.request("GET", "/api/json", action="test.failure")
    await client.close()


@pytest.mark.asyncio
async def test_invalid_stop_mode_and_admin_path() -> None:
    client = make_client(lambda request: httpx.Response(404))
    with pytest.raises(ValueError, match="mode must be"):
        await client.stop_build("demo", 1, "pause")
    with pytest.raises(ValueError, match="Jenkins-relative"):
        await client.admin_request("GET", "https://other.example/api")
    with pytest.raises(ValueError, match="Jenkins-relative"):
        await client.admin_request("GET", "relative/path")
    await client.close()


def test_empty_job_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        _job_path("///")
