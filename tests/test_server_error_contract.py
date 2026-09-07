"""MCP boundary regression tests for template validation."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from jenkins_mcp_server.client import JenkinsInputError
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
