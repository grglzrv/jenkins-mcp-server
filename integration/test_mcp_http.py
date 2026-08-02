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


async def main() -> None:
    # mcp 2.x yields two streams; 1.x also yielded a session-id callback.
    async with streamable_http_client("http://localhost:8000/mcp") as streams:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
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
            await asyncio.sleep(4)
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
