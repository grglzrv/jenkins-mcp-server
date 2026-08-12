"""Tests for the minibridge guardrail policy and chart wiring."""

import re
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "docker"
CHART = ROOT / "charts/jenkins-mcp-server"

GUARDRAILS = [
    "covert-instruction-detection",
    "schema-misuse-prevention",
    "secrets-redaction",
    "cross-origin-tool-access",
    "sensitive-pattern-detection",
    "shadowing-pattern-detection",
]

TOOL_GROUPS = ["@read", "@write", "@destructive", "@admin"]


def test_policy_and_entrypoint_exist() -> None:
    for name in ["policy.rego", "policy_test.rego", "entrypoint.sh", "Dockerfile.minibridge"]:
        assert (DOCKER / name).is_file(), f"docker/{name} missing"


def test_all_tools_smoke_covers_exact_server_surface() -> None:
    smoke = runpy.run_path(ROOT / "integration" / "minibridge_all_tools.py")
    server = (ROOT / "src" / "jenkins_mcp_server" / "server.py").read_text()
    registered = set(re.findall(r"@mcp\.tool\(\)\s*\n\s*async def ([a-z_0-9]+)", server))

    allowed = smoke["ALLOWED"]
    denied = smoke["DENIED"]
    assert not (allowed & denied)
    assert allowed | denied == registered
    assert denied == {
        "update_job_config",
        "delete_job",
        "stop_build",
        "cancel_queue_item",
        "set_node_offline",
    }


def test_all_tools_smoke_denies_only_destructive_at_minibridge() -> None:
    values = yaml.safe_load(
        (ROOT / ".github" / "smoke-values-minibridge-tools.yaml").read_text()
    )
    minibridge = values["minibridge"]
    assert minibridge["mode"] == "http"
    assert minibridge["tools"] == {"deny": ["@destructive"], "allow": []}
    assert minibridge["methodsDeny"] == []
    assert minibridge["guardrails"] == []

    mcp = values["mcp"]
    assert mcp["readOnly"] is False
    for option in [
        "allowJobWrite",
        "allowBuildWrite",
        "allowNodeWrite",
        "allowAdminRequest",
        "allowDestructive",
        "allowJobDelete",
        "allowJobUpdate",
        "allowBuildStop",
    ]:
        assert mcp[option] is True
    assert mcp["allowScriptConsole"] is False
    assert mcp["allowedJobs"] == "mcp-*"


def test_entrypoint_uses_acuvity_single_container_transport_split() -> None:
    text = (DOCKER / "entrypoint.sh").read_text()
    for var in ["JENKINS_URL", "JENKINS_USERNAME", "JENKINS_TOKEN"]:
        assert var in text, f"{var} not validated in entrypoint"
    # Minibridge owns client-facing Streamable HTTP; only its private child is
    # stdio, matching Acuvity's registry container convention.
    assert "--transport stdio" in text
    assert "unset MCP_TRANSPORT MCP_HOST MCP_PORT MCP_PATH" in text
    assert "mcp-proxy" not in text
    assert "mcp-proxy" not in (DOCKER / "Dockerfile.minibridge").read_text()


def test_policy_covers_every_declared_guardrail() -> None:
    policy = (DOCKER / "policy.rego").read_text()
    for g in GUARDRAILS:
        assert f'"{g}"' in policy, f"guardrail {g} not implemented in policy.rego"


def test_policy_knows_this_servers_tools() -> None:
    """Our own tool names must be excluded or cross-origin detection misfires."""
    server = (ROOT / "src/jenkins_mcp_server/server.py").read_text()
    tools = re.findall(r"@mcp\.tool\(\)\s*\n\s*async def ([a-z_0-9]+)", server)
    policy = (DOCKER / "policy.rego").read_text()
    assert tools, "no tools found in server.py"
    missing = [t for t in tools if f'"{t}"' not in policy]
    assert not missing, f"tools missing from policy exclude list: {missing}"


def test_every_tool_belongs_to_exactly_one_group() -> None:
    """The chart advertises groups; they must cover the real tool surface."""
    server = (ROOT / "src/jenkins_mcp_server/server.py").read_text()
    tools = set(re.findall(r"@mcp\.tool\(\)\s*\n\s*async def ([a-z_0-9]+)", server))
    policy = (DOCKER / "policy.rego").read_text()
    block = policy.split("_tool_groups := {")[1].split("\n_all_tools")[0]
    groups = {
        m.group(1): set(re.findall(r'"([a-z_0-9]+)"', m.group(2)))
        for m in re.finditer(r'"(@\w+)": \{([^}]*)\}', block, re.S)
    }
    assert set(groups) == set(TOOL_GROUPS)
    grouped = set().union(*groups.values())
    assert tools == grouped, f"group drift: {tools ^ grouped}"
    for a in groups:
        for b in groups:
            if a < b:
                assert not groups[a] & groups[b], f"{a} and {b} overlap"


def test_chart_defaults_allow_every_tool_and_capability() -> None:
    mb = yaml.safe_load((CHART / "values.yaml").read_text())["minibridge"]
    assert mb["tools"]["deny"] == [], "default must allow all tools"
    assert mb["tools"]["allow"] == [], "an empty allow list means no restriction"
    assert mb["methodsDeny"] == [], "default must allow all capabilities"


def test_chart_documents_the_exclude_destructive_case() -> None:
    values = (CHART / "values.yaml").read_text()
    assert "@destructive" in values
    for group in TOOL_GROUPS:
        assert group in values, f"group {group} not documented in values.yaml"


def test_tool_policy_is_passed_to_the_container() -> None:
    deployment = (CHART / "templates/_helpers.tpl").read_text()
    for env in ["TOOLS_DENY", "TOOLS_ALLOW", "METHODS_DENY"]:
        assert env in deployment, f"{env} not wired into the Deployment"
    entrypoint = (DOCKER / "entrypoint.sh").read_text()
    for env in ["TOOLS_DENY", "TOOLS_ALLOW", "METHODS_DENY"]:
        assert f"REGO_POLICY_RUNTIME_{env}" in entrypoint


def test_chart_exposes_minibridge_options() -> None:
    mb = yaml.safe_load((CHART / "values.yaml").read_text())["minibridge"]
    assert mb["enabled"] is False, "minibridge must be opt-in"
    assert mb["policer"]["enforce"] is True
    assert mb["guardrails"] == []
    assert mb["basicAuth"]["enabled"] is False


def test_chart_covers_the_upstream_minibridge_surface() -> None:
    """Keys the Acuvity reference chart exposes should not be missing here."""
    mb = yaml.safe_load((CHART / "values.yaml").read_text())["minibridge"]
    for key in ["mode", "log", "tracing", "tls", "sbom", "guardrails",
                "basicAuth", "policer"]:
        assert key in mb, f"minibridge.{key} missing"
    assert mb["policer"]["rego"]["enabled"] is True
    assert mb["policer"]["http"]["enabled"] is False
    assert mb["mode"] == "http"
    assert mb["sbom"] is True
    assert mb["tls"]["enabled"] is False


def test_no_secret_material_is_inlined_in_minibridge_values() -> None:
    """Secrets are referenced from Secrets, never given raw values."""
    mb = yaml.safe_load((CHART / "values.yaml").read_text())["minibridge"]
    assert "value" not in mb["basicAuth"], "basicAuth must not accept a raw value"
    assert "value" not in mb["policer"]["http"]["token"]
    assert "existingSecret" in mb["policer"]["http"]["token"]
    for k in ["cert", "key"]:
        assert k not in mb["tls"], f"tls.{k} must not accept inline material"
    assert "value" not in mb["tls"]["pass"]
    assert set(mb["tls"]["pass"]["valueFrom"]) == {"name", "key"}


def test_policer_env_uses_minibridge_variable_names() -> None:
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    for var in ["MINIBRIDGE_MODE", "MINIBRIDGE_LISTEN", "MINIBRIDGE_HEALTH_LISTEN",
                "MINIBRIDGE_ENDPOINT_MCP",
                "MINIBRIDGE_LOG_LEVEL", "MINIBRIDGE_POLICER_TYPE",
                "MINIBRIDGE_POLICER_ENFORCE", "MINIBRIDGE_POLICER_REGO_POLICY",
                "MINIBRIDGE_POLICER_HTTP_URL",
                "MINIBRIDGE_POLICER_HTTP_BEARER_TOKEN",
                "MINIBRIDGE_POLICER_HTTP_CA", "MINIBRIDGE_MCP_USE_TEMPDIR",
                "MINIBRIDGE_TLS_SERVER_CERT", "MINIBRIDGE_TLS_SERVER_KEY",
                "MINIBRIDGE_TLS_SERVER_CLIENT_CA", "OTEL_EXPORTER_OTLP_ENDPOINT"]:
        assert var in helpers, f"{var} not emitted by the minibridge env helper"


def test_chart_guardrail_enum_matches_the_policy() -> None:
    import json

    schema = json.loads((CHART / "values.schema.json").read_text())
    enum = schema["properties"]["minibridge"]["properties"]["guardrails"]["items"]["enum"]
    assert sorted(enum) == sorted(GUARDRAILS)


def test_token_is_never_inlined_in_the_chart() -> None:
    """JENKINS_TOKEN must always arrive via secretKeyRef, never a literal."""
    deployment = (CHART / "templates/deployment.yaml").read_text()
    idx = deployment.index("JENKINS_TOKEN")
    window = deployment[idx : idx + 240]
    assert "secretKeyRef" in window
    assert "value:" not in window.split("secretKeyRef")[0]


def test_basic_auth_secret_key_is_validated_against_external_secret() -> None:
    validate = (CHART / "templates/_validate.tpl").read_text()
    assert "CreateContainerConfigError" in validate
    assert "extraData" in validate


@pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not installed")
def test_opa_policy_tests_pass() -> None:
    result = subprocess.run(
        ["opa", "test", str(DOCKER)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout


def test_every_tool_is_described_for_the_agent() -> None:
    """Descriptions are the in-band signal an agent uses to choose a tool.

    Without them a model sees only names, and get_job, get_job_config and
    get_build_info are not distinguishable from names alone.
    """
    import asyncio

    from jenkins_mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    undescribed = sorted(t.name for t in tools if not (t.description or "").strip())
    assert not undescribed, f"tools with no description: {undescribed}"


def test_destructive_tools_say_so_in_their_description() -> None:
    """The policy layers can refuse a call; only the description can stop the
    model choosing it in the first place."""
    import asyncio
    import re

    from jenkins_mcp_server.server import mcp

    # Derive the set from the policy rather than restating it, so a tool moved
    # into @destructive is required to gain a warning too.
    policy = (DOCKER / "policy.rego").read_text()
    block = policy.split("_tool_groups := {")[1].split("\n_all_tools")[0]
    destructive = set(
        re.findall(
            r'"([a-z_0-9]+)"',
            re.search(r'"@destructive": \{([^}]*)\}', block, re.S).group(1),
        )
    )
    assert destructive, "could not read @destructive from policy.rego"

    consequences = {
        "update_job_config": ("destructive", "overwritten", "absent", "lost"),
        "delete_job": ("destructive", "build history", "external backup"),
        "stop_build": ("destructive", "abandoned", "lost"),
        "cancel_queue_item": ("destructive", "discarded", "queue item"),
        "set_node_offline": ("destructive", "no new work", "stall"),
    }
    assert set(consequences) == destructive, (
        "every @destructive tool needs a reviewed, consequence-specific warning"
    )
    tools = {
        t.name: " ".join((t.description or "").lower().split())
        for t in asyncio.run(mcp.list_tools())
    }
    for name, required in consequences.items():
        missing = [phrase for phrase in required if phrase not in tools[name]]
        assert not missing, f"{name} description is missing consequences: {missing}"


def test_admin_escape_hatch_warns_about_ungated_mutations() -> None:
    """The generic admin tool can bypass operation-specific destructive flags."""
    import asyncio

    from jenkins_mcp_server.server import mcp

    tools = {
        t.name: " ".join((t.description or "").lower().split())
        for t in asyncio.run(mcp.list_tools())
    }
    description = tools["jenkins_admin_request"]
    for phrase in [
        "mutate",
        "delete",
        "not gated by mcp_allow_destructive",
        "mcp_allowed_jobs",
        "mcp_allow_script_console",
        "sensitive-pattern guardrail",
        "confirm",
    ]:
        assert phrase in description, f"admin description is missing {phrase!r}"


def test_destructive_descriptions_name_every_server_policy_gate() -> None:
    """A single enabled flag is not enough; descriptions must not imply it is."""
    import asyncio

    from jenkins_mcp_server.server import mcp

    tools = {
        t.name: " ".join((t.description or "").lower().split())
        for t in asyncio.run(mcp.list_tools())
    }
    required = {
        "update_job_config": (
            "mcp_allow_job_write",
            "mcp_allow_destructive",
            "mcp_allow_job_update",
        ),
        "delete_job": (
            "mcp_allow_job_write",
            "mcp_allow_destructive",
            "mcp_allow_job_delete",
        ),
        "stop_build": (
            "mcp_allow_build_write",
            "mcp_allow_destructive",
            "mcp_allow_build_stop",
        ),
        "cancel_queue_item": (
            "mcp_allow_build_write",
            "mcp_allow_destructive",
            "mcp_allow_build_stop",
        ),
        "set_node_offline": ("mcp_allow_node_write", "mcp_allow_destructive"),
    }
    for name, gates in required.items():
        missing = [gate for gate in gates if gate not in tools[name]]
        assert not missing, f"{name} description is missing policy gates: {missing}"


def test_copy_description_matches_jenkins_copy_semantics() -> None:
    """Jenkins copies config.xml; it does not force the target disabled."""
    import asyncio

    from jenkins_mcp_server.server import mcp

    tools = {
        t.name: " ".join((t.description or "").lower().split())
        for t in asyncio.run(mcp.list_tools())
    }
    description = tools["copy_job"]
    assert "inherits" in description
    assert "enabled or disabled state" in description
    assert "build history" in description and "not copied" in description
    assert "job/extendedread" in description
    assert "job/create" in description
    assert "job/configure" in description and "redact" in description


def test_creation_descriptions_keep_plaintext_secrets_out_of_tool_arguments() -> None:
    """Credential IDs are references; descriptions must not invite raw secrets."""
    import asyncio

    from jenkins_mcp_server.server import mcp

    tools = {
        t.name: " ".join((t.description or "").lower().split())
        for t in asyncio.run(mcp.list_tools())
    }
    for name in ["create_job_from_xml", "update_job_config", "create_pipeline_job"]:
        assert "plaintext" in tools[name] and "credential" in tools[name]
    multibranch = tools["create_multibranch_pipeline"]
    for phrase in ["credentials_id", "stored in jenkins", "never pass", "private key"]:
        assert phrase in multibranch


def test_body_carrying_tools_advertise_the_request_limit() -> None:
    """Agents should learn the boundary before generating an oversized body."""
    import asyncio

    from jenkins_mcp_server.server import mcp

    tools = {
        t.name: " ".join((t.description or "").lower().split())
        for t in asyncio.run(mcp.list_tools())
    }
    for name in [
        "create_job_from_xml",
        "update_job_config",
        "create_pipeline_job",
        "create_multibranch_pipeline",
        "trigger_build",
        "jenkins_admin_request",
    ]:
        assert "mcp_max_request_bytes" in tools[name], name
