import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def call(session, name, args):
    """Call a tool and fail loudly if the server refused it.

    call_tool returns is_error rather than raising, so an unchecked call makes a
    denied tool look like a passing test.
    """
    result = await session.call_tool(name, args)
    if result.is_error:
        text = " ".join(
            getattr(c, "text", "") for c in (result.content or [])
        ).strip()
        raise AssertionError(f"{name} failed: {text or result}")
    return result


async def wait_for_build(session, job_name, build_number, timeout=120):
    """Block until the build record exists in Jenkins.

    trigger_build only enqueues. The build leaves the queue and gets its number
    some seconds later, depending on how quickly Jenkins allocates an executor,
    so anything that addresses the build by number has to wait for it rather
    than sleeping a fixed interval.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    last = None
    while asyncio.get_event_loop().time() < deadline:
        result = await session.call_tool(
            "get_build_info", {"job_name": job_name, "build_number": build_number}
        )
        if not result.is_error:
            return result
        last = " ".join(getattr(c, "text", "") for c in (result.content or [])).strip()
        await asyncio.sleep(2)
    raise AssertionError(
        f"{job_name} #{build_number} did not start within {timeout}s: {last}"
    )


async def main() -> None:
    # mcp 2.x yields two streams; 1.x also yielded a session-id callback.
    async with streamable_http_client("http://localhost:8000/mcp") as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            undescribed = sorted(
                tool.name for tool in tools.tools if not (tool.description or "").strip()
            )
            assert not undescribed, f"direct MCP tools lack descriptions: {undescribed}"
            required = {
                "create_pipeline_job",
                "trigger_build",
                "list_running_builds",
                "stop_build",
                "delete_job",
                "create_multibranch_pipeline",
                "update_job_config",
            }
            assert required <= names
            descriptions = {
                tool.name: (tool.description or "").lower() for tool in tools.tools
            }
            assert "irreversible" in descriptions["delete_job"]
            assert "not gated by mcp_allow_destructive" in descriptions[
                "jenkins_admin_request"
            ]
            assert "mcp_allowed_jobs" in descriptions["jenkins_admin_request"]
            assert "mcp_allow_script_console" in descriptions["jenkins_admin_request"]

            script = """pipeline {
  agent any
  stages {
    stage("test") {
      steps {
        sh "sleep 20"
      }
    }
  }
}"""
            await call(
                session,
                "create_pipeline_job",
                {"job_name": "mcp-smoke", "jenkinsfile": script},
            )
            await call(
                session,
                "trigger_build",
                {"job_name": "mcp-smoke"},
            )
            # The pipeline sleeps 20s, so once the build exists there is time to
            # observe and stop it.
            await wait_for_build(session, "mcp-smoke", 1)
            running = await call(session, "list_running_builds", {})
            print(running)
            await call(
                session,
                "stop_build",
                {"job_name": "mcp-smoke", "build_number": 1},
            )
            await call(
                session,
                "delete_job",
                {"job_name": "mcp-smoke"},
            )


asyncio.run(main())
