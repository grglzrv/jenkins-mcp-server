from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from .audit import AuditLogger
from .client import JenkinsClient
from .config import Settings, get_settings
from .diagnostics import JenkinsContact
from .security import Policy
from .templates import multibranch_github_xml, pipeline_job_xml


def create_client(
    settings: Settings,
    audit: AuditLogger | None = None,
    contact: JenkinsContact | None = None,
) -> JenkinsClient:
    policy = Policy(
        read_only=settings.read_only,
        allow_job_write=settings.allow_job_write,
        allow_build_write=settings.allow_build_write,
        allow_node_write=settings.allow_node_write,
        allow_admin_request=settings.allow_admin_request,
        allow_script_console=settings.allow_script_console,
        job_patterns=settings.job_patterns,
        allow_destructive=settings.allow_destructive,
        allow_job_delete=settings.allow_job_delete,
        allow_job_update=settings.allow_job_update,
        allow_build_stop=settings.allow_build_stop,
    )
    audit_logger = audit or AuditLogger(
        settings.audit_log_path,
        settings.audit_max_bytes,
        settings.audit_backup_count,
    )
    if audit is None:
        audit_logger.probe()
    return JenkinsClient(settings, policy, audit_logger, contact=contact)


@lru_cache
def get_audit_logger() -> AuditLogger:
    settings = get_settings()
    audit = AuditLogger(
        settings.audit_log_path,
        settings.audit_max_bytes,
        settings.audit_backup_count,
    )
    audit.probe()
    return audit


@lru_cache
def get_jenkins_contact() -> JenkinsContact:
    return JenkinsContact()


@lru_cache
def get_client() -> JenkinsClient:
    return create_client(get_settings(), get_audit_logger(), get_jenkins_contact())


@asynccontextmanager
async def server_lifespan(_: MCPServer[None]) -> AsyncIterator[None]:
    client = get_client()
    try:
        yield None
    finally:
        await client.close()
        get_client.cache_clear()
        get_jenkins_contact.cache_clear()


# MCPServer takes the version directly, so clients see the server they are
# actually talking to in `initialize` -> serverInfo rather than the SDK version.
# stateless_http moved from the constructor to the transport call in mcp 2.x.
mcp = MCPServer("Jenkins MCP Server", version=__version__, lifespan=server_lifespan)


@mcp.tool()
async def list_jobs(folder: str | None = None) -> Any:
    """List jobs visible to this server, optionally within a folder.

    Returns name, full path, URL and build colour. Pass a folder's full name,
    for example "Platform", to list its immediate children. Jobs outside
    MCP_ALLOWED_JOBS are omitted rather than reported as errors."""
    return await get_client().list_jobs(folder)


@mcp.tool()
async def get_job(job_name: str) -> Any:
    """Get one job's current state: description, buildable flag, health and
    the most recent build references. Use get_job_config for its XML
    definition, or get_build_info for a specific build."""
    return await get_client().get_job(job_name)


@mcp.tool()
async def get_job_config(job_name: str) -> str:
    """Fetch a job's config.xml definition.

    Requires Job/ExtendedRead in Jenkins; Job/Read alone is not enough and
    fails with 403 while other tools keep working."""
    return await get_client().get_job_config(job_name)


@mcp.tool()
async def create_job_from_xml(job_name: str, config_xml: str) -> Any:
    """Create a job from a raw config.xml.

    Fails if the name already exists. Prefer create_pipeline_job or
    create_multibranch_pipeline unless you need full control of the XML. Do
    not place plaintext credentials in the XML; reference Jenkins-managed
    credential IDs. The encoded body must fit MCP_MAX_REQUEST_BYTES."""
    return await get_client().create_job(job_name, config_xml)


@mcp.tool()
async def update_job_config(job_name: str, config_xml: str) -> Any:
    """Replace a job's config.xml in full.

    Destructive: the previous definition is overwritten with no history kept
    by this server, and any setting absent from the XML you supply is lost.
    Read the current config first with get_job_config, and do not place
    plaintext credentials in the XML. Requires MCP_ALLOW_JOB_WRITE,
    MCP_ALLOW_DESTRUCTIVE and MCP_ALLOW_JOB_UPDATE. The encoded body must fit
    MCP_MAX_REQUEST_BYTES."""
    return await get_client().update_job(job_name, config_xml)


@mcp.tool()
async def delete_job(job_name: str) -> Any:
    """Delete a job and all of its build history.

    Destructive and irreversible through this server: Jenkins core removes the
    job and its builds, so recovery requires an external backup. Disabled by
    default; requires MCP_ALLOW_JOB_WRITE, MCP_ALLOW_DESTRUCTIVE and
    MCP_ALLOW_JOB_DELETE. Confirm with get_job first."""
    return await get_client().delete_job(job_name)


@mcp.tool()
async def copy_job(source_job: str, target_job: str) -> Any:
    """Copy an existing job's configuration to a new name.

    The target inherits the source's settings, including its enabled or
    disabled state; build history and workspaces are not copied. Both source
    and target must be inside MCP_ALLOWED_JOBS. Jenkins requires
    Job/ExtendedRead on the source and Job/Create on the target parent; it also
    requires source Job/Configure when extended-read redacts secrets."""
    return await get_client().copy_job(source_job, target_job)


@mcp.tool()
async def enable_job(job_name: str) -> Any:
    """Enable a job so Jenkins will build it. Safe to call when already enabled."""
    return await get_client().enable_job(job_name, True)


@mcp.tool()
async def disable_job(job_name: str) -> Any:
    """Disable a job so Jenkins stops building it.

    Queued builds are not cancelled; use cancel_queue_item for those. The job
    and its history are kept, so this is reversible with enable_job."""
    return await get_client().enable_job(job_name, False)


@mcp.tool()
async def create_pipeline_job(
    job_name: str,
    jenkinsfile: str,
    description: str = "Managed by Jenkins MCP",
) -> Any:
    """Create a Pipeline job from an inline Jenkinsfile.

    The script runs in the Groovy sandbox. Requires workflow-job and
    workflow-cps, both included in the workflow-aggregator plugin. Reference
    Jenkins credential IDs; do not put plaintext secrets in the Jenkinsfile.
    The generated XML must fit MCP_MAX_REQUEST_BYTES."""
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
    """Create a multibranch Pipeline that discovers branches from a Git
    repository.

    Requires the workflow-multibranch, branch-api and git plugins. Run
    scan_multibranch_pipeline afterwards to populate branches immediately.
    repository_url must not contain embedded credentials, a query string, or a
    fragment. credentials_id names a credential already stored in Jenkins;
    never pass a token, password or private key in that field. script_path must
    be a canonical repository-relative path. The generated XML must fit
    MCP_MAX_REQUEST_BYTES."""
    config_xml = multibranch_github_xml(
        repository_url,
        credentials_id,
        script_path,
        description,
    )
    return await get_client().create_job(job_name, config_xml)


@mcp.tool()
async def scan_multibranch_pipeline(job_name: str) -> Any:
    """Trigger a branch scan on a multibranch Pipeline so newly pushed
    branches are discovered without waiting for the next scheduled scan."""
    return await get_client().scan_multibranch(job_name)


@mcp.tool()
async def trigger_build(
    job_name: str,
    parameters: dict[str, Any] | None = None,
) -> Any:
    """Queue a build.

    Returns queue_url, not a build number: the build has not started yet. Poll
    get_queue, or use get_build_info once it has. For a parameterised job pass
    parameters, using an empty object to accept every default; omitting it
    entirely makes Jenkins reject the trigger. Their encoded form must fit
    MCP_MAX_REQUEST_BYTES."""
    return await get_client().build(job_name, parameters)


@mcp.tool()
async def stop_build(
    job_name: str,
    build_number: int,
    mode: str = "stop",
) -> Any:
    """Stop a running build.

    Destructive: the build is abandoned and marked ABORTED, and any work it
    had done is lost. mode escalates from stop to term and then kill. Freestyle
    builds support only stop; term and kill are Pipeline-only and require
    workflow-job. Requires MCP_ALLOW_BUILD_WRITE, MCP_ALLOW_DESTRUCTIVE and
    MCP_ALLOW_BUILD_STOP."""
    return await get_client().stop_build(job_name, build_number, mode)


@mcp.tool()
async def get_build_info(
    job_name: str,
    build_number: int | str = "lastBuild",
) -> Any:
    """Get one build's result, timing, duration and parameters.

    build_number accepts a number or an alias such as lastBuild,
    lastSuccessfulBuild or lastFailedBuild."""
    return await get_client().build_info(job_name, build_number)


@mcp.tool()
async def get_build_console(
    job_name: str,
    build_number: int | str = "lastBuild",
    start: int = 0,
) -> Any:
    """Read a build's console output.

    Output is truncated to MCP_MAX_LOG_BYTES; pass the returned next_start back
    as start to continue reading, which is how a running build is followed.
    build_number accepts a number or an alias such as lastBuild."""
    return await get_client().console(job_name, build_number, start)


@mcp.tool()
async def list_running_builds() -> Any:
    """List builds currently executing across the controller, with their job
    and build number. Jobs outside MCP_ALLOWED_JOBS are omitted. Use
    get_build_console to follow one."""
    return await get_client().running_builds()


@mcp.tool()
async def get_queue() -> Any:
    """List builds waiting to start, with why each is blocked.

    A queue item is not a build yet and has no build number; it gains one
    when an executor picks it up. Items outside MCP_ALLOWED_JOBS are omitted."""
    return await get_client().queue()


@mcp.tool()
async def cancel_queue_item(item_id: int) -> Any:
    """Remove a queued item before it starts.

    Destructive: the request to build is discarded. Takes the queue item id
    from get_queue, which is not a build number. Use stop_build for a build
    that is already running. Requires MCP_ALLOW_BUILD_WRITE,
    MCP_ALLOW_DESTRUCTIVE and MCP_ALLOW_BUILD_STOP."""
    return await get_client().cancel_queue(item_id)


@mcp.tool()
async def list_nodes() -> Any:
    """List build nodes with their capacity, idle and offline state.

    Executor/current-build details are intentionally excluded; use
    list_running_builds for running jobs filtered through MCP_ALLOWED_JOBS."""
    return await get_client().nodes()


@mcp.tool()
async def get_node(node_name: str) -> Any:
    """Get one node's capacity, idle state and offline reason.

    Current-build details are intentionally excluded; use list_running_builds
    for running jobs filtered through MCP_ALLOWED_JOBS. Node names are case
    sensitive and must not be empty."""
    return await get_client().node_info(node_name)


@mcp.tool()
async def set_node_offline(
    node_name: str,
    offline: bool,
    message: str = "Managed by MCP",
) -> Any:
    """Take a node offline, or bring it back online.

    Taking a node offline is destructive: running builds keep going but no
    new work is scheduled there, which can stall a pipeline. Requires
    MCP_ALLOW_NODE_WRITE, and taking offline additionally requires
    MCP_ALLOW_DESTRUCTIVE."""
    return await get_client().toggle_node(node_name, offline, message)


@mcp.tool()
async def jenkins_admin_request(
    method: str,
    path: str,
    body: str | None = None,
    content_type: str = "application/json",
) -> Any:
    """Send an arbitrary authenticated request to a Jenkins path.

    A powerful escape hatch for endpoints no other tool covers, disabled by
    default and requiring MCP_ALLOW_ADMIN_REQUEST. MCP_ALLOWED_JOBS still
    applies to job URLs. The Groovy console additionally requires
    MCP_ALLOW_SCRIPT_CONSOLE and remains blocked by Minibridge when its
    sensitive-pattern guardrail is active. Non-read methods can mutate or
    delete Jenkins state and are not gated by MCP_ALLOW_DESTRUCTIVE; confirm the
    exact method, path and body first. path must be Jenkins-relative and
    absolute, for example /api/json. Session and CSRF headers are withheld from
    the response. Unlike typed mutation tools, raw 3xx responses are returned
    for the caller to interpret. The encoded body must fit
    MCP_MAX_REQUEST_BYTES."""
    return await get_client().admin_request(method, path, body, content_type)
