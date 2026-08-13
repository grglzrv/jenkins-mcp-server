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
