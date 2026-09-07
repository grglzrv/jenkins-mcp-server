"""MCP boundary regression tests for safe, actionable tool failures."""

import httpx
import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from jenkins_mcp_server import server
from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.client import JenkinsClient, JenkinsInputError
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.security import Policy
from jenkins_mcp_server.server import _build_template
from jenkins_mcp_server.templates import multibranch_github_xml, pipeline_job_xml


def test_valid_template_is_unchanged_at_mcp_boundary() -> None:
    xml = _build_template(pipeline_job_xml, "echo 'ok'", "safe")
    assert "CpsFlowDefinition" in xml
    assert "echo 'ok'" in xml


def test_template_validation_is_an_expected_mcp_tool_error() -> None:
    marker = "SHOULD-NOT-ECHO-SECRET"
    with pytest.raises(JenkinsInputError, match="embedded credentials") as captured:
        _build_template(
            multibranch_github_xml,
            f"https://{marker}@github.com/acme/repo.git",
            "",
            "Jenkinsfile",
            "safe",
        )

    assert isinstance(captured.value, ToolError)
    assert marker not in str(captured.value)


def test_script_path_validation_stays_actionable() -> None:
    with pytest.raises(JenkinsInputError, match="script_path"):
        _build_template(
            multibranch_github_xml,
            "https://github.com/acme/repo.git",
            "",
            "../Jenkinsfile",
            "safe",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport", "http", "policy", "input", "unexpected"])
async def test_real_mcp_calls_preserve_safe_errors_and_hide_crashes(monkeypatch, failure) -> None:
    marker = "MCP-ERROR-SECRET-MARKER"

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "transport":
            raise httpx.ConnectError(f"proxy credentials: {marker}", request=request)
        if failure == "unexpected":
            raise RuntimeError(marker)
        return httpx.Response(403, text=f"plugin diagnostics: {marker}")

    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="admin",
        JENKINS_TOKEN="token",
        JENKINS_MAX_RETRIES=0,
    )
    policy = Policy(
        read_only=False,
        allow_job_write=True,
        allow_build_write=True,
        allow_node_write=True,
        allow_admin_request=True,
        job_patterns=["*"],
    )
    jc = JenkinsClient(settings, policy, AuditLogger(None), transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "get_client", lambda: jc)
    # Use the production tool functions with the SDK's public in-memory client:
    # assertions cover serialized CallToolResult errors, not just inheritance.
    app = MCPServer("error-contract")
    app.add_tool(server.jenkins_admin_request)
    app.add_tool(server.delete_job)
    app.add_tool(server.create_multibranch_pipeline)
    name = "jenkins_admin_request"
    arguments = {"method": "GET", "path": f"/plugin/{marker}/status?token={marker}"}
    expected = "Jenkins request failed due to a transport error"
    if failure == "http":
        expected = "Jenkins returned 403"
    elif failure == "policy":
        name, arguments = "delete_job", {"job_name": "smoke"}
        expected = "disabled by MCP_ALLOW_DESTRUCTIVE"
    elif failure == "input":
        name = "create_multibranch_pipeline"
        arguments = {
            "job_name": "smoke",
            "repository_url": f"https://{marker}@github.com/acme/repo.git",
        }
        expected = "embedded credentials"
    elif failure == "unexpected":
        expected = "Error executing tool jenkins_admin_request"
    try:
        async with Client(app) as client:
            result = await client.call_tool(name, arguments)
        assert result.is_error is True
        text = " ".join(getattr(block, "text", "") for block in result.content)
        assert expected in text
        assert marker not in text
    finally:
        await jc.close()
