"""Regression tests for job scoping, path validation, and bounded responses."""

import gzip
import json
from collections.abc import Callable

import httpx
import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.security import Policy, PolicyError


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    patterns: list[str] | None = None,
    max_log_bytes: int = 1_000_000,
) -> JenkinsClient:
    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="u",
        JENKINS_TOKEN="t",
        JENKINS_MAX_RETRIES=0,
        MCP_MAX_LOG_BYTES=max_log_bytes,
    )
    policy = Policy(
        read_only=False,
        allow_job_write=True,
        allow_build_write=True,
        allow_node_write=True,
        allow_admin_request=True,
        job_patterns=patterns or ["AI/*"],
        allow_destructive=True,
        allow_job_delete=True,
    )
    return JenkinsClient(settings, policy, AuditLogger(), httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_jobs_only_returns_allowed_jobs_and_ancestors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/json":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"name": "AI", "fullName": "AI"},
                        {"name": "Production", "fullName": "Production"},
                    ]
                },
            )
        if request.url.path == "/job/AI/api/json":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"name": "build", "fullName": "AI/build"},
                        {"name": "nested", "fullName": "AI/nested"},
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = make_client(handler)
    assert [job["name"] for job in (await client.list_jobs())["jobs"]] == ["AI"]
    assert [job["name"] for job in (await client.list_jobs("AI"))["jobs"]] == [
        "build",
        "nested",
    ]
    with pytest.raises(PolicyError):
        await client.list_jobs("Production")
    await client.close()


@pytest.mark.asyncio
async def test_queue_and_running_builds_are_filtered_by_job_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/queue/api/json":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": 1, "task": {"fullName": "AI/build"}},
                        {"id": 2, "task": {"fullName": "Production/secret"}},
                        {
                            "id": 3,
                            "task": {
                                "url": "https://ci.example.com/jenkins/job/AI/job/proxy-build/"
                            },
                        },
                    ]
                },
            )
        if request.url.path == "/computer/api/json":
            return httpx.Response(
                200,
                json={
                    "computer": [
                        {
                            "displayName": "agent",
                            "executors": [
                                {
                                    "currentExecutable": {
                                        "url": "https://ci.example.com/jenkins/job/AI/job/build/1/"
                                    }
                                },
                                {
                                    "currentExecutable": {
                                        "url": "https://ci.example.com/jenkins/job/Production/job/secret/2/"
                                    }
                                },
                            ],
                            "oneOffExecutors": [],
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = make_client(handler)
    # Jenkins' advertised public root may differ from the internal connection
    # URL. Only its decoded job path is used for authorization; it is never
    # fetched by this client.
    assert [item["id"] for item in (await client.queue())["items"]] == [1, 3]
    running = await client.running_builds()
    assert [build["url"] for build in running] == [
        "https://ci.example.com/jenkins/job/AI/job/build/1/"
    ]
    await client.close()


@pytest.mark.asyncio
async def test_node_tools_do_not_leak_out_of_scope_executables() -> None:
    """Node status must not bypass MCP_ALLOWED_JOBS through executor details."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/computer/api/json":
            assert request.url.params["depth"] == "0"
            assert "currentExecutable" not in request.url.params["tree"]
            return httpx.Response(
                200,
                json={
                    "busyExecutors": 1,
                    "totalExecutors": 2,
                    "computer": [
                        {
                            "displayName": "agent",
                            "offline": False,
                            "idle": False,
                            "temporarilyOffline": False,
                            "numExecutors": 2,
                            "executors": [
                                {
                                    "currentExecutable": {
                                        "fullDisplayName": "Production/secret #9",
                                        "url": (
                                            "https://jenkins.test/job/Production/"
                                            "job/secret/9/"
                                        ),
                                    }
                                }
                            ],
                        }
                    ],
                },
            )
        if request.url.path == "/computer/agent/api/json":
            assert request.url.params["depth"] == "0"
            assert "currentExecutable" not in request.url.params["tree"]
            return httpx.Response(
                200,
                json={
                    "displayName": "agent",
                    "offline": False,
                    "idle": False,
                    "temporarilyOffline": False,
                    "numExecutors": 2,
                    "executors": [
                        {
                            "currentExecutable": {
                                "fullDisplayName": "Production/secret #9",
                                "url": (
                                    "https://jenkins.test/job/Production/"
                                    "job/secret/9/"
                                ),
                            }
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = make_client(handler)
    listed = await client.nodes()
    detail = await client.node_info("agent")

    assert listed["busyExecutors"] == 1
    assert listed["computer"][0]["displayName"] == "agent"
    assert detail["displayName"] == "agent"
    assert "Production/secret" not in json.dumps([listed, detail])
    assert "currentExecutable" not in json.dumps([listed, detail])
    await client.close()


@pytest.mark.asyncio
async def test_cancel_queue_resolves_job_before_mutation() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/queue/item/42/api/json":
            return httpx.Response(
                200,
                json={"task": {"fullName": "Production/secret"}},
            )
        raise AssertionError("the disallowed queue item must not be cancelled")

    client = make_client(handler)
    with pytest.raises(PolicyError, match="not allowed"):
        await client.cancel_queue(42)
    assert paths == ["/queue/item/42/api/json"]
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector",
    ["../../api", "../computer", "/1", "1?depth=100", "1#fragment", "latest"],
)
async def test_build_selector_cannot_inject_a_path(selector: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid selectors must fail before an HTTP request")

    client = make_client(handler)
    with pytest.raises(ValueError, match="build_number"):
        await client.build_info("AI/build", selector)
    with pytest.raises(ValueError, match="build_number"):
        await client.console("AI/build", selector)
    await client.close()


class TwoChunkStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.second_chunk_requested = False

    async def __aiter__(self):
        yield b"a" * 2048
        self.second_chunk_requested = True
        yield b"b" * 2048


@pytest.mark.asyncio
async def test_console_stops_streaming_at_the_configured_limit() -> None:
    stream = TwoChunkStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=stream,
            headers={"X-Text-Size": "4096", "X-More-Data": "false"},
        )

    client = make_client(handler, max_log_bytes=1024)
    result = await client.console("AI/build", 1)
    assert len(result["text"]) == 1024
    assert result["truncated"] is True
    assert stream.second_chunk_requested is False
    await client.close()


@pytest.mark.asyncio
async def test_console_does_not_redecode_streamed_gzip_content() -> None:
    content = b"Jenkins console output\n"
    compressed = gzip.compress(content)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=compressed,
            headers={
                "Content-Encoding": "gzip",
                "Content-Length": str(len(compressed)),
                "X-Text-Size": str(len(content)),
                "X-More-Data": "false",
            },
        )

    client = make_client(handler, max_log_bytes=1024)
    result = await client.console("AI/build", 1)
    assert result["text"] == content.decode()
    assert result["truncated"] is False
    await client.close()


# --- a caller-named queue item is authorized from our own origin ------------


def _item_client(payload: dict[str, object]) -> JenkinsClient:
    return make_client(lambda request: httpx.Response(200, json=payload))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "task_url"),
    [
        ("queue_item", "https://evil.test/job/AI/job/x/"),
        ("queue_item", "https://jenkins.test:8443/job/AI/job/x/"),
        ("queue_item", "http://jenkins.test/job/AI/job/x/"),
        ("cancel_queue", "https://evil.test/job/AI/job/x/"),
    ],
)
async def test_item_lookups_do_not_trust_a_foreign_task_url(
    method: str, task_url: str
) -> None:
    """These act on an item the caller named, so the verdict must come from a
    response this server asked for.

    A task URL on another host reads as the permitted job AI/x while naming a
    host we never contacted. The queue listing is different: it only omits what
    it cannot authorize, so it keeps using Jenkins' advertised root.
    """
    client = _item_client({"id": 7, "task": {"name": "x", "url": task_url}})
    with pytest.raises(PolicyError):
        await getattr(client, method)(7)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_url",
    ["https://jenkins.test/job/AI/job/nightly/", "/job/AI/job/nightly/"],
)
async def test_same_origin_and_relative_task_urls_still_resolve(
    task_url: str,
) -> None:
    """Jenkins returns both forms; neither may break."""
    client = _item_client({"id": 7, "task": {"name": "nightly", "url": task_url}})
    assert (await client.queue_item(7))["id"] == 7


@pytest.mark.asyncio
async def test_full_name_is_preferred_over_a_foreign_url() -> None:
    """fullName comes from Jenkins' model rather than from a URL, so a
    differing advertised root does not block a legitimate lookup."""
    client = _item_client(
        {
            "id": 7,
            "task": {
                "name": "nightly",
                "fullName": "AI/nightly",
                "url": "https://ci.example.com/jenkins/job/AI/job/nightly/",
            },
        }
    )
    assert (await client.queue_item(7))["id"] == 7
