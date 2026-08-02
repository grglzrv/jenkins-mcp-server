import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    async with streamablehttp_client("http://localhost:8000/mcp") as streams:
        read_stream, write_stream, _ = streams
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
            await session.call_tool(
                "create_pipeline_job",
                {"job_name": "mcp-smoke", "jenkinsfile": script},
            )
            await session.call_tool(
                "trigger_build",
                {"job_name": "mcp-smoke"},
            )
            await asyncio.sleep(4)
            running = await session.call_tool("list_running_builds", {})
            print(running)
            await session.call_tool(
                "stop_build",
                {"job_name": "mcp-smoke", "build_number": 1},
            )
            await session.call_tool(
                "delete_job",
                {"job_name": "mcp-smoke"},
            )


asyncio.run(main())
