"""Tests for the minibridge guardrail policy and chart wiring."""

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


def test_entrypoint_requires_credentials_and_forces_stdio() -> None:
    text = (DOCKER / "entrypoint.sh").read_text()
    for var in ["JENKINS_URL", "JENKINS_USERNAME", "JENKINS_TOKEN"]:
        assert var in text, f"{var} not validated in entrypoint"
    # minibridge owns the listener; the server must not bind a port itself.
    assert "--transport stdio" in text


def test_policy_covers_every_declared_guardrail() -> None:
    policy = (DOCKER / "policy.rego").read_text()
    for g in GUARDRAILS:
        assert f'"{g}"' in policy, f"guardrail {g} not implemented in policy.rego"


def test_policy_knows_this_servers_tools() -> None:
    """Our own tool names must be excluded or cross-origin detection misfires."""
    import re

    server = (ROOT / "src/jenkins_mcp_server/server.py").read_text()
    tools = re.findall(r"@mcp\.tool\(\)\s*\n\s*async def ([a-z_0-9]+)", server)
    policy = (DOCKER / "policy.rego").read_text()
    assert tools, "no tools found in server.py"
    missing = [t for t in tools if f'"{t}"' not in policy]
    assert not missing, f"tools missing from policy exclude list: {missing}"


def test_every_tool_belongs_to_exactly_one_group() -> None:
    """The chart advertises groups; they must cover the real tool surface."""
    import re

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
    for k in ["cert", "key", "pass"]:
        assert k not in mb["tls"], f"tls.{k} must not accept inline material"


def test_policer_env_uses_minibridge_variable_names() -> None:
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    for var in ["MINIBRIDGE_MODE", "MINIBRIDGE_LISTEN", "MINIBRIDGE_HEALTH_LISTEN",
                "MINIBRIDGE_LOG_LEVEL", "MINIBRIDGE_POLICER_TYPE",
                "MINIBRIDGE_POLICER_ENFORCE", "MINIBRIDGE_POLICER_REGO_POLICY",
                "MINIBRIDGE_POLICER_URL", "MINIBRIDGE_POLICER_TOKEN",
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
