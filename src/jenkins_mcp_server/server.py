from __future__ import annotations

from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from .audit import AuditLogger
from .client import JenkinsClient
from .config import Settings, get_settings
from .security import Policy
from .templates import multibranch_github_xml, pipeline_job_xml


def create_client(settings: Settings) -> JenkinsClient:
    policy = Policy(
        read_only=settings.read_only,
        allow_job_write=settings.allow_job_write,
        allow_build_write=settings.allow_build_write,
        allow_node_write=settings.allow_node_write,
        allow_admin_request=settings.allow_admin_request,
        job_patterns=settings.job_patterns,
        allow_destructive=settings.allow_destructive,
        allow_job_delete=settings.allow_job_delete,
        allow_job_update=settings.allow_job_update,
        allow_build_stop=settings.allow_build_stop,
    )
    return JenkinsClient(settings, policy, AuditLogger(settings.audit_log_path))


@lru_cache
def get_client() -> JenkinsClient:
    return create_client(get_settings())


# MCPServer takes the version directly, so clients see the server they are
# actually talking to in `initialize` -> serverInfo rather than the SDK version.
# stateless_http moved from the constructor to the transport call in mcp 2.x.
mcp = MCPServer("Jenkins MCP Server", version=__version__)


@mcp.tool()
async def list_jobs(folder: str | None = None) -> Any:
    return await get_client().list_jobs(folder)


@mcp.tool()
async def get_job(job_name: str) -> Any:
    return await get_client().get_job(job_name)


@mcp.tool()
async def get_job_config(job_name: str) -> str:
    return await get_client().get_job_config(job_name)


@mcp.tool()
async def create_job_from_xml(job_name: str, config_xml: str) -> Any:
    return await get_client().create_job(job_name, config_xml)


@mcp.tool()
async def update_job_config(job_name: str, config_xml: str) -> Any:
    return await get_client().update_job(job_name, config_xml)


@mcp.tool()
async def delete_job(job_name: str) -> Any:
    return await get_client().delete_job(job_name)


@mcp.tool()
async def copy_job(source_job: str, target_job: str) -> Any:
    return await get_client().copy_job(source_job, target_job)


@mcp.tool()
async def enable_job(job_name: str) -> Any:
    return await get_client().enable_job(job_name, True)


@mcp.tool()
async def disable_job(job_name: str) -> Any:
    return await get_client().enable_job(job_name, False)


@mcp.tool()
async def create_pipeline_job(
    job_name: str,
    jenkinsfile: str,
    description: str = "Managed by Jenkins MCP",
) -> Any:
    config_xml = pipeline_job_xml(jenkinsfile, description)
    return await get_client().create_job(job_name, config_xml)


@mcp.tool()
async def create_multibranch_pipeline(
    job_name: str,
    repository_url: str,
    credentials_id: str = "",
    script_path: str = "Jenkinsfile",
    description: str = "Managed by Jenkins MCP",
) -> Any:
    config_xml = multibranch_github_xml(
        repository_url,
        credentials_id,
        script_path,
        description,
    )
    return await get_client().create_job(job_name, config_xml)


@mcp.tool()
async def scan_multibranch_pipeline(job_name: str) -> Any:
    return await get_client().scan_multibranch(job_name)


@mcp.tool()
async def trigger_build(
    job_name: str,
    parameters: dict[str, Any] | None = None,
) -> Any:
    return await get_client().build(job_name, parameters)


@mcp.tool()
async def stop_build(
    job_name: str,
    build_number: int,
    mode: str = "stop",
) -> Any:
    return await get_client().stop_build(job_name, build_number, mode)


@mcp.tool()
async def get_build_info(
    job_name: str,
    build_number: int | str = "lastBuild",
) -> Any:
    return await get_client().build_info(job_name, build_number)


@mcp.tool()
async def get_build_console(
    job_name: str,
    build_number: int | str = "lastBuild",
    start: int = 0,
) -> Any:
    return await get_client().console(job_name, build_number, start)


@mcp.tool()
async def list_running_builds() -> Any:
    return await get_client().running_builds()


@mcp.tool()
async def get_queue() -> Any:
    return await get_client().queue()


@mcp.tool()
async def cancel_queue_item(item_id: int) -> Any:
    return await get_client().cancel_queue(item_id)


@mcp.tool()
async def list_nodes() -> Any:
    return await get_client().nodes()


@mcp.tool()
async def get_node(node_name: str) -> Any:
    return await get_client().node_info(node_name)


@mcp.tool()
async def set_node_offline(
    node_name: str,
    offline: bool,
    message: str = "Managed by MCP",
) -> Any:
    return await get_client().toggle_node(node_name, offline, message)


@mcp.tool()
async def jenkins_admin_request(
    method: str,
    path: str,
    body: str | None = None,
    content_type: str = "application/json",
) -> Any:
    return await get_client().admin_request(method, path, body, content_type)
