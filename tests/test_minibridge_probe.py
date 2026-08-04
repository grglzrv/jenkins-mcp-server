"""Regression tests for the Minibridge smoke probe's MCP error handling."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest
from mcp.shared.exceptions import MCPError

PROBE = runpy.run_path(Path(__file__).parents[1] / "integration" / "minibridge_probe.py")
call = PROBE["call"]


class RaisingSession:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def call_tool(self, name: str, arguments: dict) -> None:
        raise self.error


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
async def test_raised_jenkins_mcp_errors_mean_policy_allowed(message: str) -> None:
    session = RaisingSession(MCPError(code=-32000, message=message))

    was_refused, detail = await call(session, "list_jobs", {})

    assert was_refused is False
    assert detail == message[:120]


async def test_explicit_minibridge_policy_error_means_refused() -> None:
    message = "request blocked: connections to delete_job are not permitted by policy"
    session = RaisingSession(MCPError(code=451, message=message))

    was_refused, detail = await call(session, "delete_job", {"job_name": "smoke"})

    assert was_refused is True
    assert detail == message


async def test_non_policy_mcp_error_is_not_a_refusal() -> None:
    message = "Jenkins returned HTTP 403"
    session = RaisingSession(MCPError(code=-32000, message=message))

    was_refused, _ = await call(session, "list_jobs", {})

    assert was_refused is False


async def test_non_mcp_exception_is_not_swallowed() -> None:
    session = RaisingSession(RuntimeError("test harness failed"))

    with pytest.raises(RuntimeError, match="test harness failed"):
        await call(session, "list_jobs", {})
