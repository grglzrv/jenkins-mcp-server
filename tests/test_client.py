import httpx
import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient, _job_path
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.security import Policy, PolicyError


def settings() -> Settings:
    return Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="admin",
        JENKINS_TOKEN="token",
        JENKINS_MAX_RETRIES=0,
    )


def policy(**overrides: object) -> Policy:
    values: dict[str, object] = {
        "read_only": False,
        "allow_job_write": True,
        "allow_build_write": True,
        "allow_node_write": True,
        "allow_admin_request": False,
        "job_patterns": ["*"],
        # These suites exercise destructive paths, so opt in explicitly.
        "allow_job_delete": True,
    }
    values.update(overrides)
    return Policy(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_build_stop_delete_and_tls_client() -> None:
    seen: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.headers)))
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(
                200,
                json={
                    "crumbRequestField": "Jenkins-Crumb",
                    "crumb": "abc",
                },
            )
        if request.url.path == "/createItem":
            return httpx.Response(200)
        if request.url.path == "/job/demo/build":
            return httpx.Response(
                201,
                headers={"Location": "https://jenkins.test/queue/item/1/"},
            )
        if request.url.path == "/job/demo/7/stop":
            return httpx.Response(200)
        if request.url.path == "/job/demo/doDelete":
            return httpx.Response(302)
        return httpx.Response(404, text="unexpected")

    client = JenkinsClient(
        settings(),
        policy(),
        AuditLogger(),
        httpx.MockTransport(handler),
    )
    await client.create_job("demo", "<project/>")
    result = await client.build("demo")
    await client.stop_build("demo", 7)
    await client.delete_job("demo")

    assert result["queue_url"].endswith("/queue/item/1/")
    assert all(
        headers.get("jenkins-crumb") == "abc"
        for method, _, headers in seen
        if method == "POST"
    )
    await client.close()


@pytest.mark.asyncio
async def test_console_is_paginated_and_bounded() -> None:
    client_settings = settings()
    client_settings.max_log_bytes = 5

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/job/demo/1/logText/progressiveText":
            return httpx.Response(
                200,
                content=b"123456789",
                headers={
                    "X-Text-Size": "9",
                    "X-More-Data": "true",
                },
            )
        return httpx.Response(404)

    client = JenkinsClient(
        client_settings,
        policy(),
        AuditLogger(),
        httpx.MockTransport(handler),
    )
    output = await client.console("demo", 1)
    assert output["text"] == "12345"
    assert output["truncated"] is True
    assert output["more_data"] is True
    await client.close()


def test_nested_job_encoding() -> None:
    assert _job_path("folder/a b") == "job/folder/job/a%20b"


def test_policy_blocks_writes_and_job_scope() -> None:
    read_only_policy = policy(read_only=True)
    with pytest.raises(PolicyError):
        read_only_policy.require_write("job", "demo")

    scoped_policy = policy(job_patterns=["team-a/*"])
    with pytest.raises(PolicyError):
        scoped_policy.check_job("team-b/x")
