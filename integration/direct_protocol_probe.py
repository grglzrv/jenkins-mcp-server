"""Verify the installed direct server's protocol contract without a Jenkins write."""

import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOLS = {
    "list_jobs", "get_job", "get_job_config", "create_job_from_xml",
    "update_job_config", "delete_job", "copy_job", "enable_job", "disable_job",
    "create_pipeline_job", "create_multibranch_pipeline", "scan_multibranch_pipeline",
    "trigger_build", "stop_build", "get_build_info", "get_build_console",
    "list_running_builds", "get_queue", "get_queue_item", "cancel_queue_item",
    "list_nodes", "get_node", "set_node_offline", "jenkins_admin_request",
}


async def probe(expected_version: str, url: str) -> None:
    async with streamable_http_client(url) as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "Jenkins MCP Server", initialized
            assert initialized.server_info.version == expected_version, initialized
            await session.send_ping()
            tools = (await session.list_tools()).tools
            names = {tool.name for tool in tools}
            assert names == EXPECTED_TOOLS, (
                f"missing={sorted(EXPECTED_TOOLS - names)}, "
                f"unexpected={sorted(names - EXPECTED_TOOLS)}"
            )
            assert len(tools) == len(names), "duplicate tool registrations"
            for tool in tools:
                assert (tool.description or "").strip(), f"missing description: {tool.name}"
                assert tool.input_schema.get("type") == "object", tool
            # This name is deliberately outside the smoke chart's AI/* scope.
            # A passing refusal proves tools/call works without touching Jenkins.
            denied = await session.call_tool("get_job", {"job_name": "Outside/protocol-smoke"})
            assert denied.is_error, "out-of-scope Jenkins job was not refused"
            text = " ".join(getattr(block, "text", "") for block in denied.content)
            assert "MCP_ALLOWED_JOBS" in text, text
            print(
                f"PASS direct MCP {expected_version}: initialize, ping, {len(tools)} tools, policy"
            )


async def main(expected_version: str, url: str) -> None:
    await asyncio.wait_for(probe(expected_version, url), timeout=30)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: direct_protocol_probe.py EXPECTED_VERSION [URL]")
    endpoint = sys.argv[2] if len(sys.argv) == 3 else "http://127.0.0.1:8000/mcp"
    asyncio.run(main(sys.argv[1], endpoint))
