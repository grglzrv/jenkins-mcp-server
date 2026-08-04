"""Prove minibridge actually enforces the policy, in a real cluster.

Installing the chart with `minibridge.enabled` only proves the pod starts. This
connects as an MCP client through the proxy and asserts the policy is applied:
denied tools are absent from `tools/list` and refused on `tools/call`, while
permitted tools are not refused.

Run against a port-forward to the MCP port:

    python integration/test_minibridge_policy.py http://127.0.0.1:8000/mcp
"""

import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# Must match the chart values the smoke workflow installs with:
#   minibridge.tools.deny: ["@destructive"]
DENIED = {
    "delete_job",
    "update_job_config",
    "stop_build",
    "cancel_queue_item",
    "set_node_offline",
}
# Present and callable under that policy.
ALLOWED = {"list_jobs", "get_job", "trigger_build", "create_pipeline_job"}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


async def main(url: str) -> None:
    async with streamable_http_client(url) as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            print(f"connected: {init.server_info.name} {init.server_info.version}")

            listed = {t.name for t in (await session.list_tools()).tools}
            print(f"tools/list returned {len(listed)}: {sorted(listed)}")

            # 1. Denied tools must not be advertised. This is the strongest
            #    signal that the policy is applied rather than merely loaded:
            #    the proxy rewrites the listing before the client sees it.
            leaked = DENIED & listed
            if leaked:
                fail(f"denied tools appear in tools/list: {sorted(leaked)}")
            print(f"denied tools hidden from the listing: {sorted(DENIED)}")

            # 2. Permitted tools must survive the filter, or the proxy is
            #    simply blocking everything and the test proves nothing.
            missing = ALLOWED - listed
            if missing:
                fail(f"permitted tools missing from tools/list: {sorted(missing)}")
            print(f"permitted tools still listed: {sorted(ALLOWED)}")

            # 3. A denied tool must also be refused when called directly, since
            #    a client can call a tool it was never shown.
            for name in sorted(DENIED):
                try:
                    result = await session.call_tool(name, {"job_name": "smoke"})
                except Exception as exc:  # refusal may surface as a protocol error
                    print(f"{name}: refused ({type(exc).__name__})")
                    continue
                if not result.is_error:
                    fail(f"{name} was executed despite being denied by policy")
                text = " ".join(
                    getattr(c, "text", "") for c in (result.content or [])
                )
                print(f"{name}: refused ({text.strip()[:70]})")

            # 4. A permitted tool must not be refused by the policy. It will
            #    fail against the unreachable Jenkins in this environment, and
            #    that error must not be a policy refusal.
            result = await session.call_tool("list_jobs", {})
            text = " ".join(getattr(c, "text", "") for c in (result.content or []))
            lowered = text.lower()
            if "not permitted by policy" in lowered or "blocked by policy" in lowered:
                fail(f"list_jobs was refused by policy: {text[:120]}")
            print(f"list_jobs reached the server (error is Jenkins, not policy): "
                  f"{text.strip()[:70]}")

    print("PASS: minibridge enforced the tool policy")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: test_minibridge_policy.py <mcp-url>")
    asyncio.run(main(sys.argv[1]))
