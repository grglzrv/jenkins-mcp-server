"""Fail-closed handling for malformed or over-broad Jenkins read responses."""

from collections.abc import Callable

import httpx
import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient, JenkinsError
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.security import Policy


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> JenkinsClient:
    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="user",
        JENKINS_TOKEN="token",
        JENKINS_MAX_RETRIES=0,
    )
    policy = Policy(
        read_only=False,
        allow_job_write=True,
        allow_build_write=True,
        allow_node_write=True,
        allow_admin_request=True,
        job_patterns=["AI/*"],
    )
    return JenkinsClient(settings, policy, AuditLogger(), httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"jobs": {"fullName": "Production/secret"}},
    ],
)
async def test_list_jobs_fails_closed_on_malformed_job_collection(payload: object) -> None:
    client = make_client(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(JenkinsError, match="malformed job-list JSON"):
        await client.list_jobs()

    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"items": {"task": {"fullName": "Production/secret"}}},
    ],
)
async def test_queue_fails_closed_on_malformed_queue_collection(payload: object) -> None:
    client = make_client(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(JenkinsError, match="malformed queue JSON"):
        await client.queue()

    await client.close()


@pytest.mark.asyncio
async def test_queue_omits_invalid_names_and_projects_documented_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        tree = request.url.params.get("tree", "")
        assert "actions" not in tree
        assert "params" not in tree
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": 1,
                        "why": "waiting for an executor",
                        "task": {
                            "name": "build",
                            "fullName": "AI/build",
                            "url": "https://jenkins.test/job/AI/job/build/",
                            "secretPluginField": "must not escape",
                        },
                        "actions": [{"parameters": [{"value": "queue-secret"}]}],
                    },
                    {"id": 2, "task": {"fullName": "AI/../Production/secret"}},
                ],
                "unexpectedTopLevel": "must not escape",
            },
        )

    client = make_client(handler)
    result = await client.queue()

    assert result == {
        "items": [
            {
                "id": 1,
                "why": "waiting for an executor",
                "task": {
                    "name": "build",
                    "fullName": "AI/build",
                    "url": "https://jenkins.test/job/AI/job/build/",
                },
            }
        ]
    }
    assert "secret" not in str(result).lower()
    await client.close()


@pytest.mark.asyncio
async def test_queue_item_projects_executable_and_enforces_job_allowlist() -> None:
    responses = {
        "/queue/item/7/api/json": {
            "id": 7,
            "why": None,
            "task": {
                "name": "build",
                "fullName": "AI/build",
                "url": "https://jenkins.test/job/AI/job/build/",
                "pluginSecret": "must not escape",
            },
            "executable": {
                "number": 42,
                "url": "https://jenkins.test/job/AI/job/build/42/",
                "actions": [{"secret": "must not escape"}],
            },
            "actions": [{"parameters": [{"value": "must not escape"}]}],
        },
        "/queue/item/8/api/json": {
            "id": 8,
            "task": {"fullName": "Production/secret"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses[request.url.path])

    client = make_client(handler)
    result = await client.queue_item(7)

    assert result == {
        "id": 7,
        "why": None,
        "task": {
            "name": "build",
            "fullName": "AI/build",
            "url": "https://jenkins.test/job/AI/job/build/",
        },
        "executable": {
            "number": 42,
            "url": "https://jenkins.test/job/AI/job/build/42/",
        },
    }
    assert "secret" not in str(result).casefold()
    with pytest.raises(PermissionError, match="MCP_ALLOWED_JOBS"):
        await client.queue_item(8)
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"id": 7, "task": "not-an-object"},
        {"id": 7, "task": {"fullName": "AI/build"}, "executable": []},
    ],
)
async def test_queue_item_fails_closed_on_malformed_nested_data(payload: object) -> None:
    client = make_client(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(JenkinsError, match="malformed queue JSON"):
        await client.queue_item(7)

    await client.close()


@pytest.mark.asyncio
async def test_public_projections_omit_non_finite_plugin_numbers() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            content=(
                b'{"items":[{"id":NaN,"why":"waiting",'
                b'"task":{"fullName":"AI/build"}}]}'
            ),
            headers={"Content-Type": "application/json"},
        )
    )

    result = await client.queue()

    assert result == {
        "items": [{"why": "waiting", "task": {"fullName": "AI/build"}}]
    }
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"computer": {}},
        {"computer": [None]},
        {"computer": [{"executors": {}, "oneOffExecutors": []}]},
        {
            "computer": [
                {"executors": [{"currentExecutable": "not-an-object"}], "oneOffExecutors": []}
            ]
        },
    ],
)
async def test_running_builds_wraps_malformed_nested_json(payload: object) -> None:
    client = make_client(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(JenkinsError, match="malformed running-build JSON"):
        await client.running_builds()

    await client.close()


@pytest.mark.asyncio
async def test_running_builds_projects_documented_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        tree = request.url.params.get("tree", "")
        assert "currentExecutable" in tree
        assert "actions" not in tree
        return httpx.Response(
            200,
            json={
                "computer": [
                    {
                        "displayName": "agent",
                        "executors": [
                            {
                                "currentExecutable": {
                                    "number": 7,
                                    "fullDisplayName": "AI/build #7",
                                    "url": "https://jenkins.test/job/AI/job/build/7/",
                                    "actions": [{"parameters": [{"value": "build-secret"}]}],
                                }
                            }
                        ],
                        "oneOffExecutors": [],
                        "secretPluginField": "must not escape",
                    }
                ]
            },
        )

    client = make_client(handler)
    result = await client.running_builds()

    assert result == [
        {
            "node": "agent",
            "number": 7,
            "fullDisplayName": "AI/build #7",
            "url": "https://jenkins.test/job/AI/job/build/7/",
        }
    ]
    assert "secret" not in str(result).lower()
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", ["not-an-integer", "-1", "9", "10"])
async def test_console_rejects_malformed_or_regressive_jenkins_offset(offset: str) -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            content=b"hello",
            headers={"X-Text-Size": offset, "X-More-Data": "true"},
        )
    )

    with pytest.raises(JenkinsError, match="invalid X-Text-Size"):
        await client.console("AI/build", 7, start=10)

    await client.close()


@pytest.mark.asyncio
async def test_console_accepts_advancing_jenkins_cursor_smaller_than_rendered_body() -> None:
    rendered = b"rendered console text is longer than the raw-log cursor delta"
    client = make_client(
        lambda request: httpx.Response(
            200,
            content=rendered,
            headers={"X-Text-Size": "11", "X-More-Data": "true"},
        )
    )

    result = await client.console("AI/build", 7, start=10)

    assert result["text"] == rendered.decode()
    assert result["next_start"] == 11
    assert result["more_data"] is True
    await client.close()
