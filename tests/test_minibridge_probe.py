"""Regression tests for the Minibridge smoke probe's MCP error handling."""

from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import MCPError

PROBE = runpy.run_path(Path(__file__).parents[1] / "integration" / "minibridge_probe.py")
call = PROBE["call"]
reached_jenkins = PROBE["reached_jenkins"]
refused = PROBE["refused"]
allowed = PROBE["allowed"]


class RaisingSession:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def call_tool(self, name: str, arguments: dict) -> None:
        raise self.error


def tool_error(message: str) -> SimpleNamespace:
    return SimpleNamespace(
        is_error=True,
        content=[SimpleNamespace(text=message)],
    )


@pytest.mark.parametrize(
    "message",
    [
        "[Errno -2] Name or service not known",
        "Connection refused by Jenkins",
        "Connect timeout while contacting Jenkins",
        "The Jenkins request timed out",
        "TLS handshake failed",
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
    ],
)
async def test_raised_non_policy_mcp_errors_are_inconclusive(message: str) -> None:
    session = RaisingSession(MCPError(code=-32000, message=message))

    with pytest.raises(RuntimeError, match="Inconclusive MCP failure"):
        await call(session, "list_jobs", {})


def test_sanitized_jenkins_tool_error_means_policy_allowed() -> None:
    result = tool_error(
        "Error executing tool list_jobs: "
        "Jenkins request failed due to a transport error"
    )

    assert reached_jenkins(result) is True
    assert refused(result) is False
    assert allowed(result) is True


def test_sanitized_jenkins_status_error_means_policy_allowed() -> None:
    result = tool_error(
        "Jenkins returned 403. Permission denied; check the Jenkins account's permissions."
    )

    assert reached_jenkins(result) is True
    assert refused(result) is False
    assert allowed(result) is True


def test_policy_error_with_network_word_stays_refused() -> None:
    result = tool_error(
        "request blocked: connection to delete_job is not permitted by policy"
    )

    assert reached_jenkins(result) is False
    assert refused(result) is True
    assert allowed(result) is False


async def test_explicit_minibridge_policy_error_means_refused() -> None:
    message = "request blocked: connections to delete_job are not permitted by policy"
    session = RaisingSession(MCPError(code=451, message=message))

    was_refused, detail = await call(session, "delete_job", {"job_name": "smoke"})

    assert was_refused is True
    assert detail == message


async def test_non_policy_mcp_error_is_not_a_refusal() -> None:
    message = "Jenkins returned HTTP 403"
    session = RaisingSession(MCPError(code=-32000, message=message))

    with pytest.raises(RuntimeError, match="Inconclusive MCP failure"):
        await call(session, "list_jobs", {})


class ResultSession:
    def __init__(self, result: SimpleNamespace) -> None:
        self.result = result

    async def call_tool(self, name: str, arguments: dict) -> SimpleNamespace:
        return self.result


@pytest.mark.parametrize("name", ["list_jobs", "delete_job", "get_job"])
@pytest.mark.parametrize("message", ["Internal error", "Error executing tool list_jobs", ""])
async def test_unknown_tool_errors_cannot_pass_any_policy_assertion(name, message) -> None:
    result = tool_error(message)
    assert refused(result) is False
    assert allowed(result) is False
    with pytest.raises(RuntimeError, match="Inconclusive tool failure"):
        await call(ResultSession(result), name, {})


@pytest.mark.parametrize(
    "message",
    [
        "request blocked: Jenkins request failed is not permitted by policy",
        "Destructive action 'job.delete' is disabled by MCP_ALLOW_DESTRUCTIVE",
        "Server is in read-only mode",
        "Write category 'job' is disabled",
        "Job '../../secrets/master.key' contains path traversal segments and is rejected",
        "Job 'smoke/should-not-run' is not allowed by MCP_ALLOWED_JOBS",
    ],
)
async def test_explicit_tool_policy_errors_are_refused(message) -> None:
    result = tool_error(message)
    assert reached_jenkins(result) is False
    assert allowed(result) is False
    was_refused, _ = await call(ResultSession(result), "delete_job", {})
    assert was_refused is True


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(is_error=False, content=[]),
        tool_error("Jenkins request failed due to a transport error"),
        tool_error("Jenkins returned 503."),
    ],
)
async def test_success_and_known_jenkins_failures_prove_allowed_route(result) -> None:
    was_refused, _ = await call(ResultSession(result), "list_jobs", {})
    assert was_refused is False


async def test_non_mcp_exception_is_not_swallowed() -> None:
    session = RaisingSession(RuntimeError("test harness failed"))

    with pytest.raises(RuntimeError, match="test harness failed"):
        await call(session, "list_jobs", {})
