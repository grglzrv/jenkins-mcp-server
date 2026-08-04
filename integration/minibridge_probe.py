"""Assert that minibridge is actually enforcing, not merely running.

A pod that starts and answers a health check proves nothing about the policy in
front of it. This speaks MCP through the proxy and checks the decisions:

  * denied tools are absent from tools/list, not just refused on call
  * calling a denied tool is refused
  * an allowed tool is not refused by policy
  * a guardrail pattern in tool arguments is blocked

Run against the proxy's MCP endpoint:

    python integration/minibridge_probe.py http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

# Matches examples/values/minibridge.yaml: tools.deny is ["@destructive"].
DENIED = {
    "delete_job",
    "update_job_config",
    "stop_build",
    "cancel_queue_item",
    "set_node_offline",
}
EXPECTED_VISIBLE = {"list_jobs", "get_job", "trigger_build", "get_build_console"}

failures: list[str] = []


def check(condition: bool, description: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {description}")
    if not condition:
        failures.append(description)


# The smoke cluster has no Jenkins, so an allowed tool fails on connection.
# For CallToolResult errors, classify by what a Jenkins transport failure looks
# like. Raised MCP errors use Minibridge's explicit convention below instead of
# treating every exception as a refusal.
JENKINS_ERRORS = (
    "connect",
    "connection",
    "resolve",
    "timeout",
    "timed out",
    "name or service not known",
    "temporary failure in name resolution",
    "getaddrinfo",
    "ssl",
    "certificate",
)

# Minibridge follows HTTP semantics for an enforced policer verdict: it returns
# JSON-RPC error code 451 with a message prefixed by "request blocked:". Match
# both parts so an arbitrary MCP/server error is never mistaken for a policy
# refusal merely because ClientSession raised it.
MINIBRIDGE_POLICY_ERROR_CODE = 451
MINIBRIDGE_POLICY_ERROR_PREFIX = "request blocked:"


def error_text(result) -> str:
    return " ".join(getattr(c, "text", "") for c in (result.content or [])).lower()


def reached_jenkins(result) -> bool:
    """The call got past policy and failed trying to talk to Jenkins."""
    return any(marker in error_text(result) for marker in JENKINS_ERRORS)


def raised_mcp_refused(exc: MCPError) -> bool:
    """Classify a JSON-RPC exception raised by an MCP tool call.

    Jenkins transport failures are proof that Minibridge allowed the call to
    reach the server. Minibridge refusals use its explicit code/prefix pair.
    Other MCP errors are application/server failures, not evidence of policy
    rejection.
    """
    detail = str(exc).lower()
    return (
        exc.code == MINIBRIDGE_POLICY_ERROR_CODE
        and detail.startswith(MINIBRIDGE_POLICY_ERROR_PREFIX)
    )


def refused(result) -> bool:
    """Rejected before reaching Jenkins, by the proxy or the server."""
    return bool(result.is_error) and not reached_jenkins(result)


def allowed(result) -> bool:
    """Not rejected by policy. Failing to reach Jenkins still counts."""
    return not result.is_error or reached_jenkins(result)


async def call(session, name: str, arguments: dict):
    """Return (refused, detail).

    A refusal can arrive two ways: minibridge rejects the request at the
    protocol level and the client raises, while the server's own policy returns
    a result carrying is_error. Both count as refused.
    """
    try:
        result = await session.call_tool(name, arguments)
    except MCPError as exc:
        return raised_mcp_refused(exc), str(exc)[:120]
    return refused(result), error_text(result)[:120]


async def main(url: str) -> int:
    async with streamable_http_client(url) as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            print("tools/list")
            listed = {t.name for t in (await session.list_tools()).tools}
            print(f"  {len(listed)} tools visible")
            check(
                not (listed & DENIED),
                f"denied tools hidden from tools/list (leaked: {sorted(listed & DENIED)})",
            )
            check(
                EXPECTED_VISIBLE <= listed,
                f"allowed tools still listed (missing: {sorted(EXPECTED_VISIBLE - listed)})",
            )

            print("tools/call on a denied tool")
            was_refused, detail = await call(
                session, "delete_job", {"job_name": "smoke/should-not-run"}
            )
            check(was_refused, f"delete_job refused (got: {detail})")

            print("tools/call on an allowed tool")
            was_refused, detail = await call(session, "list_jobs", {})
            check(
                not was_refused,
                f"list_jobs not refused by policy (got: {detail})",
            )

            print("guardrail: sensitive pattern in arguments")
            was_refused, detail = await call(
                session, "get_job", {"job_name": "../../secrets/master.key"}
            )
            check(was_refused, f"path traversal blocked (got: {detail})")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("minibridge is enforcing the configured policy")
    return 0


if __name__ == "__main__":
    endpoint = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"
    raise SystemExit(asyncio.run(main(endpoint)))
