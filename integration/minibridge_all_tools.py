"""Exercise every non-destructive Jenkins tool through Minibridge.

The target deployment enables every server-side tool category. Minibridge is
therefore the only layer denying ``@destructive``. This proves both halves of
the contract:

* every destructive tool is hidden from ``tools/list`` and rejected with
  Minibridge's explicit policy error; and
* every other tool reaches and works against a real disposable Jenkins.
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

DENIED = {
    "update_job_config",
    "delete_job",
    "stop_build",
    "cancel_queue_item",
    "set_node_offline",
}

ALLOWED = {
    "list_jobs",
    "get_job",
    "get_job_config",
    "create_job_from_xml",
    "copy_job",
    "enable_job",
    "disable_job",
    "create_pipeline_job",
    "create_multibranch_pipeline",
    "scan_multibranch_pipeline",
    "trigger_build",
    "get_build_info",
    "get_build_console",
    "list_running_builds",
    "get_queue",
    "list_nodes",
    "get_node",
    "jenkins_admin_request",
}

MINIBRIDGE_POLICY_ERROR_CODE = 451
MINIBRIDGE_POLICY_ERROR_PREFIX = "request blocked:"


def result_text(result) -> str:
    return " ".join(getattr(item, "text", "") for item in (result.content or [])).strip()


async def call_allowed(
    session: ClientSession,
    called: set[str],
    name: str,
    arguments: dict,
):
    try:
        result = await session.call_tool(name, arguments)
    except MCPError as exc:
        raise AssertionError(f"allowed tool {name} was rejected: {exc}") from exc
    if result.is_error:
        raise AssertionError(f"allowed tool {name} failed in Jenkins: {result_text(result)}")
    called.add(name)
    print(f"  PASS  {name}")
    return result


async def expect_minibridge_refusal(
    session: ClientSession,
    name: str,
    arguments: dict,
) -> None:
    try:
        result = await session.call_tool(name, arguments)
    except MCPError as exc:
        detail = str(exc).lower()
        assert exc.code == MINIBRIDGE_POLICY_ERROR_CODE, (
            f"{name} returned MCP error {exc.code}, expected policy code "
            f"{MINIBRIDGE_POLICY_ERROR_CODE}: {exc}"
        )
        assert detail.startswith(MINIBRIDGE_POLICY_ERROR_PREFIX), (
            f"{name} did not return an explicit Minibridge refusal: {exc}"
        )
        print(f"  PASS  {name} refused by Minibridge")
        return
    raise AssertionError(
        f"destructive tool {name} reached Jenkins instead of being refused: "
        f"{result_text(result) or result}"
    )


async def expect_application_rejection(
    session: ClientSession,
    name: str,
    arguments: dict,
    expected: str,
) -> None:
    """Prove invalid input reaches the server but never reaches Jenkins."""
    try:
        result = await session.call_tool(name, arguments)
    except MCPError as exc:
        detail = str(exc)
        assert exc.code != MINIBRIDGE_POLICY_ERROR_CODE, (
            f"{name} was blocked by Minibridge instead of validated by the server: {exc}"
        )
        assert expected in detail, f"{name} returned the wrong validation error: {exc}"
    else:
        assert result.is_error, f"{name} accepted invalid input: {result_text(result) or result}"
        detail = result_text(result)
        assert expected in detail, f"{name} returned the wrong validation error: {detail}"
    print(f"  PASS  {name} rejected invalid input before Jenkins")


async def wait_for_build(
    session: ClientSession,
    called: set[str],
    job_name: str,
    build_number: int,
    timeout: int = 120,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    last = ""
    while asyncio.get_running_loop().time() < deadline:
        try:
            result = await session.call_tool(
                "get_build_info",
                {"job_name": job_name, "build_number": build_number},
            )
        except MCPError as exc:
            raise AssertionError(f"get_build_info was rejected: {exc}") from exc
        if not result.is_error:
            called.add("get_build_info")
            print("  PASS  get_build_info")
            return
        last = result_text(result)
        await asyncio.sleep(2)
    raise AssertionError(
        f"{job_name} #{build_number} did not start within {timeout}s: {last}"
    )


async def main(url: str) -> int:
    called: set[str] = set()
    async with streamable_http_client(url) as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            listed = {tool.name for tool in (await session.list_tools()).tools}
            assert listed == ALLOWED, (
                "tools/list did not expose exactly the non-destructive surface; "
                f"missing={sorted(ALLOWED - listed)}, unexpected={sorted(listed - ALLOWED)}"
            )
            print(f"PASS  tools/list exposes all {len(ALLOWED)} allowed tools and no others")

            print("destructive policy")
            blocked_calls = {
                "update_job_config": {
                    "job_name": "does-not-exist",
                    "config_xml": "<project/>",
                },
                "delete_job": {"job_name": "does-not-exist"},
                "stop_build": {"job_name": "does-not-exist", "build_number": 999},
                "cancel_queue_item": {"item_id": 999999},
                "set_node_offline": {"node_name": "does-not-exist", "offline": True},
            }
            for name, arguments in blocked_calls.items():
                await expect_minibridge_refusal(session, name, arguments)

            print("job read/write tools")
            freestyle_xml = """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <actions/>
  <description>Minibridge all-tools smoke job</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <scm class="hudson.scm.NullSCM"/>
  <canRoam>true</canRoam>
  <disabled>false</disabled>
  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>
  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>
  <triggers/>
  <concurrentBuild>false</concurrentBuild>
  <builders/>
  <publishers/>
  <buildWrappers/>
</project>"""
            await call_allowed(
                session,
                called,
                "create_job_from_xml",
                {"job_name": "mcp-xml-job", "config_xml": freestyle_xml},
            )
            await expect_application_rejection(
                session,
                "get_job",
                {"job_name": "/mcp-xml-job"},
                "leading, trailing, or repeated",
            )
            await call_allowed(session, called, "get_job", {"job_name": "mcp-xml-job"})
            await call_allowed(
                session,
                called,
                "get_job_config",
                {"job_name": "mcp-xml-job"},
            )
            await call_allowed(
                session,
                called,
                "copy_job",
                {"source_job": "mcp-xml-job", "target_job": "mcp-copy-job"},
            )
            await call_allowed(
                session, called, "disable_job", {"job_name": "mcp-copy-job"}
            )
            await call_allowed(
                session, called, "enable_job", {"job_name": "mcp-copy-job"}
            )

            print("pipeline and build tools")
            jenkinsfile = """pipeline {
  agent any
  stages {
    stage('minibridge-smoke') {
      steps {
        echo 'all allowed tools reached Jenkins through Minibridge'
        sleep time: 15, unit: 'SECONDS'
      }
    }
  }
}"""
            await call_allowed(
                session,
                called,
                "create_pipeline_job",
                {"job_name": "mcp-pipeline-job", "jenkinsfile": jenkinsfile},
            )
            await call_allowed(
                session, called, "trigger_build", {"job_name": "mcp-pipeline-job"}
            )
            await wait_for_build(session, called, "mcp-pipeline-job", 1)
            await call_allowed(session, called, "list_running_builds", {})
            await call_allowed(
                session,
                called,
                "get_build_console",
                {"job_name": "mcp-pipeline-job", "build_number": 1},
            )
            await call_allowed(session, called, "get_queue", {})

            print("multibranch tools")
            await call_allowed(
                session,
                called,
                "create_multibranch_pipeline",
                {
                    "job_name": "mcp-multibranch-job",
                    "repository_url": "https://github.com/grglzrv/jenkins-mcp-server.git",
                },
            )
            await call_allowed(
                session,
                called,
                "scan_multibranch_pipeline",
                {"job_name": "mcp-multibranch-job"},
            )

            print("controller tools")
            await call_allowed(session, called, "list_nodes", {})
            await expect_application_rejection(
                session,
                "get_node",
                {"node_name": ""},
                "node_name must not be empty",
            )
            await call_allowed(
                session, called, "get_node", {"node_name": "(built-in)"}
            )
            await call_allowed(
                session,
                called,
                "jenkins_admin_request",
                {"method": "GET", "path": "/api/json"},
            )
            await call_allowed(session, called, "list_jobs", {})

    missing = ALLOWED - called
    assert not missing, f"allowed tools not executed: {sorted(missing)}"
    print(
        f"PASS: all {len(ALLOWED)} non-destructive tools worked through Minibridge; "
        f"all {len(DENIED)} destructive tools were blocked"
    )
    return 0


if __name__ == "__main__":
    endpoint = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"
    raise SystemExit(asyncio.run(main(endpoint)))
