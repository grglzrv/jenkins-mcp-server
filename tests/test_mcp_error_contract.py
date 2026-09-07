"""MCP 2.1 regression coverage for expected, sanitized tool failures."""

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient, JenkinsError, JenkinsInputError
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.security import Policy, PolicyError


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "JENKINS_URL": "https://jenkins.test",
        "JENKINS_USERNAME": "admin",
        "JENKINS_TOKEN": "token",
        "JENKINS_MAX_RETRIES": 0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _policy() -> Policy:
    return Policy(
        read_only=False,
        allow_job_write=True,
        allow_build_write=True,
        allow_node_write=True,
        allow_admin_request=True,
        job_patterns=["*"],
    )


def _client(handler, **overrides: object) -> JenkinsClient:
    return JenkinsClient(
        _settings(**overrides),
        _policy(),
        AuditLogger(None),
        transport=httpx.MockTransport(handler),
    )


def test_expected_domain_errors_are_mcp_tool_errors() -> None:
    assert issubclass(JenkinsError, ToolError)
    assert issubclass(JenkinsError, RuntimeError)
    assert issubclass(JenkinsInputError, ToolError)
    assert issubclass(JenkinsInputError, ValueError)
    assert issubclass(PolicyError, ToolError)
    assert issubclass(PolicyError, PermissionError)


@pytest.mark.asyncio
async def test_request_limit_is_an_actionable_tool_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"oversized request reached Jenkins: {request.url}")

    jc = _client(handler, MCP_MAX_REQUEST_BYTES=1024)
    with pytest.raises(JenkinsInputError, match="MCP_MAX_REQUEST_BYTES"):
        await jc.admin_request("POST", "/api/json", "A" * 2048)
    await jc.close()


@pytest.mark.asyncio
async def test_jenkins_http_error_never_exposes_response_body() -> None:
    marker = "JENKINS-RESPONSE-SECRET-MARKER"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text=f"plugin diagnostics {marker}")

    jc = _client(handler)
    with pytest.raises(JenkinsError) as captured:
        await jc.admin_request("GET", "/readyz")
    message = str(captured.value)
    assert "Jenkins returned 404" in message
    assert marker not in message
    assert "plugin diagnostics" not in message
    await jc.close()


@pytest.mark.asyncio
async def test_transport_error_never_exposes_request_target() -> None:
    marker = "QUERY-CREDENTIAL-MARKER"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection failed for {request.url}", request=request)

    jc = _client(handler)
    with pytest.raises(JenkinsError) as captured:
        await jc.admin_request("GET", f"/readyz?token={marker}")
    message = str(captured.value)
    assert message == "Jenkins request failed for /readyz?[redacted]"
    assert marker not in message
    assert "jenkins.test" not in message
    await jc.close()
