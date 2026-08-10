"""Chart wiring tests for destructive-action flags and ExternalSecret options."""

import json
import re
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/jenkins-mcp-server"

DESTRUCTIVE_VALUES = [
    "allowDestructive",
    "allowJobDelete",
    "allowJobUpdate",
    "allowBuildStop",
]
DESTRUCTIVE_ENV = [
    "MCP_ALLOW_DESTRUCTIVE",
    "MCP_ALLOW_JOB_DELETE",
    "MCP_ALLOW_JOB_UPDATE",
    "MCP_ALLOW_BUILD_STOP",
]


def values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text())


def schema() -> dict:
    return json.loads((CHART / "values.schema.json").read_text())


def _section(text: str, start_heading: str, end_heading: str) -> str:
    """Slice a section by heading text, ignoring any emoji prefix."""
    import re

    start = re.search(rf"^## .*{re.escape(start_heading)}\s*$", text, re.M)
    end = re.search(rf"^## .*{re.escape(end_heading)}\s*$", text, re.M)
    assert start, f"no heading matching {start_heading!r}"
    assert end, f"no heading matching {end_heading!r}"
    return text[start.end() : end.start()]


def test_destructive_flags_exist_with_safe_defaults() -> None:
    mcp = values()["mcp"]
    for key in DESTRUCTIVE_VALUES:
        assert key in mcp, f"{key} missing from values.yaml"
    # Deleting a job is irreversible, so it must not be on by default.
    assert mcp["allowJobDelete"] is False
    assert mcp["allowDestructive"] is False
    assert mcp["maxResponseBytes"] == 10_000_000


def test_241_security_defaults_are_consistent_across_runtime_and_deployments() -> None:
    v = values()
    assert v["mcp"]["allowDestructive"] is False
    # External Jenkins commonly sits behind a firewall allowing only the
    # cluster. A DNS hostname cannot be selected portably by NetworkPolicy, so
    # pod-level isolation must be an explicit, fully modeled opt-in.
    assert v["networkPolicy"]["enabled"] is False
    assert v["networkPolicy"]["allowSameNamespace"] is True
    assert v["networkPolicy"]["allowInternetEgress"] is False
    assert v["audit"]["fileEnabled"] is False
    assert v["audit"]["requiredForReadiness"] is False
    assert v["audit"]["maxFileBytes"] == 52_428_800
    assert v["audit"]["backupCount"] == 3
    assert v["audit"]["storage"]["emptyDir"]["sizeLimit"] == "256Mi"

    config = (ROOT / "src/jenkins_mcp_server/config.py").read_text()
    policy = (ROOT / "src/jenkins_mcp_server/security.py").read_text()
    assert 'default=False, alias="MCP_ALLOW_DESTRUCTIVE"' in config
    assert "allow_destructive: bool = False" in policy

    for path in [ROOT / ".env.example", ROOT / "deploy/kubernetes/base/config.env"]:
        assert "MCP_ALLOW_DESTRUCTIVE=false" in path.read_text()
    assert "audit-data" not in (ROOT / "compose.yaml").read_text()
    raw_deployment = (ROOT / "deploy/kubernetes/base/deployment.yaml").read_text()
    assert "mountPath: /data" not in raw_deployment


def test_network_policy_template_remains_an_explicit_opt_in() -> None:
    template = (CHART / "templates/networkpolicy.yaml").read_text()
    assert template.startswith("{{- if .Values.networkPolicy.enabled }}")
    assert template.rstrip().endswith("{{- end }}")


def test_every_values_example_states_security_and_network_intent() -> None:
    for path in sorted(EXAMPLES.glob("*.yaml")):
        example = yaml.safe_load(path.read_text())
        assert example["mcp"]["allowDestructive"] is False, path.name
        assert example["mcp"]["maxResponseBytes"] == 10_000_000, path.name
        assert example["audit"]["fileEnabled"] is False, path.name
        policy = example["networkPolicy"]
        expected_enabled = path.name == "tailscale-production.yaml"
        assert policy["enabled"] is expected_enabled, path.name
        assert "allowSameNamespace" in policy, path.name
        assert "allowInternetEgress" in policy, path.name
        uses_tailscale = path.name == "tailscale-production.yaml"
        assert example["jenkins"]["url"].endswith(".ts.net") is uses_tailscale, path.name


def test_every_argocd_application_states_security_and_network_intent() -> None:
    for path in sorted(ARGOCD.glob("application-*.yaml")):
        application = yaml.safe_load(path.read_text())
        values_object = application["spec"]["source"]["helm"]["valuesObject"]
        assert values_object["mcp"]["allowDestructive"] is False, path.name
        assert values_object["mcp"]["maxResponseBytes"] == 10_000_000, path.name
        assert values_object["audit"]["fileEnabled"] is False, path.name
        policy = values_object["networkPolicy"]
        expected_enabled = path.name != "application-hpa-generic.yaml"
        assert policy["enabled"] is expected_enabled, path.name
        assert "allowSameNamespace" in policy, path.name
        assert "allowInternetEgress" in policy, path.name


def test_destructive_flags_are_passed_to_the_container() -> None:
    configmap = (CHART / "templates/configmap.yaml").read_text()
    for env in DESTRUCTIVE_ENV:
        assert env in configmap, f"{env} not wired into the ConfigMap"


def test_audit_health_and_rotation_are_wired_and_validated() -> None:
    configmap = (CHART / "templates/configmap.yaml").read_text()
    for env in [
        "MCP_AUDIT_LOG_PATH",
        "MCP_AUDIT_REQUIRED_FOR_READINESS",
        "MCP_AUDIT_MAX_BYTES",
        "MCP_AUDIT_BACKUP_COUNT",
    ]:
        assert env in configmap

    validate = (CHART / "templates/_validate.tpl").read_text()
    assert "requiredForReadiness=true requires audit.fileEnabled=true" in validate
    assert "maxFileBytes and audit.backupCount" in validate

    audit_schema = schema()["properties"]["audit"]
    for key in [
        "fileEnabled",
        "requiredForReadiness",
        "path",
        "maxFileBytes",
        "backupCount",
        "storage",
    ]:
        assert key in audit_schema["properties"]
        assert key in audit_schema["required"]


def test_destructive_flags_are_in_the_schema() -> None:
    mcp = schema()["properties"]["mcp"]
    for key in DESTRUCTIVE_VALUES:
        assert key in mcp["properties"]
        assert key in mcp["required"]


def test_chart_env_names_match_the_settings_aliases() -> None:
    """Guards against the chart and the app drifting apart."""
    config_src = (ROOT / "src/jenkins_mcp_server/config.py").read_text()
    for env in DESTRUCTIVE_ENV:
        assert f'alias="{env}"' in config_src, f"{env} has no matching Settings alias"


def test_extra_env_cannot_override_credentials_or_chart_policy() -> None:
    """Explicit env entries come after envFrom and would silently win."""
    validate = (CHART / "templates/_validate.tpl").read_text()
    for reserved in [
        'hasPrefix "JENKINS_"',
        'hasPrefix "MCP_"',
        'hasPrefix "MINIBRIDGE_"',
        'has $upper $extraEnvExact',
        '"OTEL_EXPORTER_OTLP_ENDPOINT"',
        '"TOOLS_DENY"',
        '"TOOLS_ALLOW"',
        '"METHODS_DENY"',
        '"GUARDRAILS"',
        '"BASIC_AUTH_SECRET"',
    ]:
        assert reserved in validate
    assert "duplicates another extraEnv entry" in validate
    extra_env = schema()["properties"]["mcp"]["properties"]["extraEnv"]
    assert extra_env["items"]["required"] == ["name"]
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "Reject extraEnv credential and policy overrides" in workflow
    assert "expected reserved extraEnv name" in workflow


# --- ExternalSecret -------------------------------------------------------


def test_credential_sources_are_mutually_exclusive() -> None:
    """All three pairings must fail the render, not resolve silently."""
    validate = (CHART / "templates/_validate.tpl").read_text()
    # One count check replaces the old matrix of pairwise exclusions.
    assert "exactly one jenkins.credentials source may be enabled" in validate
    assert "no jenkins.credentials source is enabled" in validate
    for source in ["existingSecret", "secretKeyRefs", "create", "externalSecret"]:
        assert f'"{source}"' in validate, f"{source} not counted as a source"


def test_validation_runs_before_the_secret_templates() -> None:
    """Otherwise a `required` inside secret.yaml surfaces the wrong error."""
    for name in ["secret.yaml", "externalsecret.yaml", "deployment.yaml"]:
        text = (CHART / "templates" / name).read_text()
        assert "jenkins-mcp-server.validate" in text, name


def test_external_secret_exposes_creation_options() -> None:
    es = values()["jenkins"]["credentials"]["externalSecret"]
    for key in [
        "apiVersion",
        "creationPolicy",
        "deletionPolicy",
        "secretStore",
        "targetUsernameKey",
        "targetTokenKey",
        "usernameRemoteKey",
        "tokenRemoteKey",
        "usernameRemoteProperty",
        "tokenRemoteProperty",
    ]:
        assert key in es, f"{key} missing from externalSecret values"
    assert es["secretStore"]["create"] is False
    assert es["enabled"] is False

    external_schema = schema()["properties"]["jenkins"]["properties"]["credentials"]["properties"][
        "externalSecret"
    ]
    for key in [
        "targetUsernameKey",
        "targetTokenKey",
        "usernameRemoteKey",
        "tokenRemoteKey",
    ]:
        assert key in external_schema["required"]
        assert external_schema["properties"][key]["description"]


def test_optional_secret_store_creation_is_templated() -> None:
    template = (CHART / "templates/externalsecret.yaml").read_text()
    assert ".Values.jenkins.credentials.externalSecret.secretStore.create" in template
    assert "provider" in template
    # The provider must be required when creating a store, or ESO gets a no-op.
    assert "required" in template


def test_credential_sources_own_their_key_names() -> None:
    creds = values()["jenkins"]["credentials"]
    assert creds["existingSecret"]["usernameKey"] == "JENKINS_USERNAME"
    assert creds["existingSecret"]["tokenKey"] == "JENKINS_TOKEN"
    assert "usernameKey" not in creds["create"]
    assert "tokenKey" not in creds["create"]
    external = creds["externalSecret"]
    assert external["targetUsernameKey"] == "JENKINS_USERNAME"
    assert external["targetTokenKey"] == "JENKINS_TOKEN"

    helpers = (CHART / "templates/_helpers.tpl").read_text()
    secret = (CHART / "templates/secret.yaml").read_text()
    external_template = (CHART / "templates/externalsecret.yaml").read_text()
    for key, path in [
        ("JENKINS_USERNAME", "create.usernameKey"),
        ("JENKINS_TOKEN", "create.tokenKey"),
    ]:
        assert f'default "{key}"' in helpers
        assert f'default "{key}"' in secret
        assert path in helpers and path in secret
    for path in ["externalSecret.targetUsernameKey", "externalSecret.targetTokenKey"]:
        assert path in helpers and path in external_template
    assert "existingSecret.usernameKey" not in secret
    assert "existingSecret.tokenKey" not in secret

    create_schema = schema()["properties"]["jenkins"]["properties"]["credentials"]["properties"][
        "create"
    ]
    # The template conditionally requires one complete current/legacy pair.
    # Keeping only enabled at schema level lets --reuse-values upgrades from
    # 2.1 and 2.2 reach that compatibility logic.
    assert create_schema["required"] == ["enabled"]
    assert create_schema["properties"]["usernameKey"]["deprecated"] is True
    assert create_schema["properties"]["tokenKey"]["deprecated"] is True


def test_secret_rotation_contracts_are_explicit_and_smoked() -> None:
    deployment = (CHART / "templates/deployment.yaml").read_text()
    validate = (CHART / "templates/_validate.tpl").read_text()
    workflow = (ROOT / ".github/workflows/chart-smoke.yml").read_text()
    assert "checksum/credentials" in deployment
    assert "jenkins.credentials.create.enabled" in deployment
    assert "dataFrom and extraData cannot be combined" in validate
    for marker in [
        "credential-sources:",
        "Helm-managed Secret is owned, rotated, and deleted",
        "Existing Secret rotates after restart and is never owned or deleted",
        "Split Secret refs are read independently and never owned",
        "ESO Kubernetes provider syncs and rotates credentials",
        "external-secrets/external-secrets",
        "provider.kubernetes.remoteNamespace",
        "externalSecret.targetUsernameKey=JENKINS_USERNAME",
        "externalSecret.targetTokenKey=JENKINS_TOKEN",
    ]:
        assert marker in workflow
    assert workflow.count("kubectl get nodes -o name 2>/dev/null") == 4


def test_values_and_production_example_still_match_schema() -> None:
    base = values()
    jsonschema.validate(base, schema())
    production = yaml.safe_load((ROOT / "examples/values/tailscale-production.yaml").read_text())

    def merge(a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            out = dict(a)
            for k, v in b.items():
                out[k] = merge(out.get(k), v)
            return out
        return b

    jsonschema.validate(merge(base, production), schema())


# --- GCP Secret Manager provider -----------------------------------------

GCP_EXAMPLE = ROOT / "examples/values/external-secrets-gcp-workload-identity.yaml"


def gcp_example() -> dict:
    return yaml.safe_load(GCP_EXAMPLE.read_text())


def test_gcp_example_exists_and_is_valid_yaml() -> None:
    assert GCP_EXAMPLE.is_file(), "GCP workload identity example is missing"
    assert isinstance(gcp_example(), dict)


def test_gcp_example_matches_the_values_schema() -> None:
    def merge(a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            out = dict(a)
            for k, v in b.items():
                out[k] = merge(out.get(k), v)
            return out
        return b

    jsonschema.validate(merge(values(), gcp_example()), schema())


def test_gcp_example_uses_the_documented_gcpsm_field_names() -> None:
    external = gcp_example()["jenkins"]["credentials"]["externalSecret"]
    gcpsm = external["secretStore"]["provider"]["gcpsm"]
    assert "projectID" in gcpsm
    wi = gcpsm["auth"]["workloadIdentity"]
    for field in ["clusterProjectID", "clusterName", "clusterLocation", "serviceAccountRef"]:
        assert field in wi, f"gcpsm workloadIdentity.{field} missing"


def test_gcp_cluster_store_reference_carries_a_namespace() -> None:
    """ClusterSecretStore has no namespace of its own, so ESO needs one here."""
    es = gcp_example()["jenkins"]["credentials"]["externalSecret"]
    assert es["secretStore"]["kind"] == "ClusterSecretStore"
    ref = es["secretStore"]["provider"]["gcpsm"]["auth"]["workloadIdentity"]["serviceAccountRef"]
    assert ref.get("namespace"), "ClusterSecretStore serviceAccountRef needs a namespace"


def test_gcp_example_service_account_name_is_deterministic() -> None:
    """The SA the chart creates must match the name ESO is pointed at."""
    v = gcp_example()
    ref = v["jenkins"]["credentials"]["externalSecret"]["secretStore"]["provider"]["gcpsm"]["auth"][
        "workloadIdentity"
    ]["serviceAccountRef"]
    # fullnameOverride pins the SA name; without it the SA is <release>-<chart>.
    assert v["fullnameOverride"] == ref["name"]
    assert v["serviceAccount"]["create"] is True
    assert "iam.gke.io/gcp-service-account" in v["serviceAccount"]["annotations"]


def test_gcp_example_does_not_also_create_a_helm_secret() -> None:
    creds = gcp_example()["jenkins"]["credentials"]
    # External Secrets owns the Secret, so no other source may be enabled.
    assert creds["existingSecret"]["enabled"] is False
    assert creds["externalSecret"]["enabled"] is True


def test_template_guards_cluster_store_namespace_requirements() -> None:
    template = (CHART / "templates/externalsecret.yaml").read_text()
    assert "workloadIdentity" in template
    assert "secretAccessKeySecretRef" in template
    assert template.count("ClusterSecretStore") >= 1


# --- shipped examples must stay valid ------------------------------------

EXAMPLES = ROOT / "examples/values"
DEPLOY = ROOT / "deploy/kubernetes"


def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(out.get(k), v)
        return out
    return b


def test_every_example_values_file_matches_the_schema() -> None:
    files = sorted(EXAMPLES.glob("*.yaml"))
    assert len(files) >= 6, f"expected the documented examples, found {files}"
    for f in files:
        jsonschema.validate(merge(values(), yaml.safe_load(f.read_text())), schema())


def test_examples_readme_references_every_values_file() -> None:
    readme = (ROOT / "examples/README.md").read_text()
    for f in EXAMPLES.glob("*.yaml"):
        assert f.name in readme, f"{f.name} is not documented in examples/README.md"


def test_secret_examples_cover_all_four_credential_paths() -> None:
    existing = yaml.safe_load((EXAMPLES / "existing-secret.yaml").read_text())
    assert existing["jenkins"]["credentials"]["existingSecret"]["enabled"] is True
    assert existing["jenkins"]["credentials"]["existingSecret"]["name"]

    managed = yaml.safe_load((EXAMPLES / "chart-managed-secret.yaml").read_text())
    assert managed["jenkins"]["credentials"]["create"]["enabled"] is True
    # Must be empty or the chart references that Secret instead of creating one.
    assert managed["jenkins"]["credentials"]["existingSecret"]["enabled"] is False
    # A real token must never be committed in an example.
    assert not managed["jenkins"]["credentials"]["create"]["jenkinsApiToken"]

    per_field = yaml.safe_load((EXAMPLES / "per-field-secret-refs.yaml").read_text())
    refs = per_field["jenkins"]["credentials"]["secretKeyRefs"]
    assert per_field["jenkins"]["credentials"]["existingSecret"]["enabled"] is False
    assert refs["username"]["name"] != refs["token"]["name"]


def test_enabled_existing_secret_values_are_explicit() -> None:
    files = sorted(EXAMPLES.glob("*.yaml")) + sorted((ROOT / ".github").glob("smoke-values*.yaml"))
    for path in files:
        document = yaml.safe_load(path.read_text())
        existing = document["jenkins"]["credentials"].get("existingSecret", {})
        if not existing.get("enabled"):
            continue
        assert existing.get("name"), f"{path.relative_to(ROOT)}: missing name"
        assert existing.get("usernameKey"), f"{path.relative_to(ROOT)}: missing usernameKey"
        assert existing.get("tokenKey"), f"{path.relative_to(ROOT)}: missing tokenKey"


def test_enabled_managed_and_external_secret_values_are_explicit() -> None:
    for path in sorted(EXAMPLES.glob("*.yaml")):
        document = yaml.safe_load(path.read_text())
        credentials = document["jenkins"]["credentials"]

        create = credentials.get("create", {})
        if create.get("enabled"):
            for key in ["jenkinsUserId", "jenkinsApiToken"]:
                assert key in create, f"{path.relative_to(ROOT)}: missing create.{key}"

        external = credentials.get("externalSecret", {})
        if external.get("enabled"):
            for key in [
                "targetUsernameKey",
                "targetTokenKey",
                "usernameRemoteKey",
                "tokenRemoteKey",
            ]:
                assert external.get(key), f"{path.relative_to(ROOT)}: missing externalSecret.{key}"
            assert external["targetUsernameKey"] != external["targetTokenKey"], (
                f"{path.relative_to(ROOT)}: External Secret target keys must differ"
            )


def test_single_target_key_is_rejected_for_existing_and_external_secrets() -> None:
    validate = (CHART / "templates/_validate.tpl").read_text()
    for source, username_key, token_key in [
        ("existingSecret", "usernameKey", "tokenKey"),
        ("externalSecret", "targetUsernameKey", "targetTokenKey"),
    ]:
        assert f"$creds.{source}.{username_key}" in validate
        assert f"$creds.{source}.{token_key}" in validate
        assert f"jenkins.credentials.{source}.{username_key}" in validate


def test_external_secret_quotes_and_requires_all_key_names() -> None:
    template = (CHART / "templates/externalsecret.yaml").read_text()
    for key in [
        "targetUsernameKey",
        "targetTokenKey",
        "usernameRemoteKey",
        "tokenRemoteKey",
    ]:
        line = next(line for line in template.splitlines() if f".{key}" in line)
        assert "required" in line
        assert "| quote" in line


def test_workload_quotes_credential_secret_names_and_keys() -> None:
    deployment = (CHART / "templates/deployment.yaml").read_text()
    for helper in [
        "usernameSecretName",
        "usernameSecretKey",
        "tokenSecretName",
        "tokenSecretKey",
    ]:
        line = next(line for line in deployment.splitlines() if helper in line)
        assert "| quote" in line


def test_chart_managed_credentials_name_the_user_id_and_api_token() -> None:
    create = values()["jenkins"]["credentials"]["create"]
    assert "jenkinsUserId" in create
    assert "jenkinsApiToken" in create
    assert "JENKINS_USERNAME" not in create
    assert "JENKINS_TOKEN" not in create
    assert "username" not in create
    assert "token" not in create

    secret = (CHART / "templates/secret.yaml").read_text()
    assert "$create.jenkinsUserId" in secret
    assert "$create.jenkinsApiToken" in secret


def test_legacy_chart_managed_fields_remain_accepted_for_v2() -> None:
    credentials_schema = schema()["properties"]["jenkins"]["properties"]["credentials"]
    create_schema = credentials_schema["properties"]["create"]
    for field in [
        "JENKINS_USERNAME",
        "JENKINS_TOKEN",
        "username",
        "token",
        "usernameKey",
        "tokenKey",
    ]:
        assert create_schema["properties"][field]["deprecated"] is True
    jsonschema.validate(
        {
            "enabled": True,
            "JENKINS_USERNAME": "2.2-user-id",
            "JENKINS_TOKEN": "2.2-api-token",
        },
        create_schema,
    )
    jsonschema.validate(
        {"enabled": True, "username": "2.1-user-id", "token": "2.1-api-token"},
        create_schema,
    )

    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    for field in [
        "jenkinsUserId",
        "jenkinsApiToken",
        "JENKINS_USERNAME",
        "JENKINS_TOKEN",
        "username",
        "token",
    ]:
        assert f"jenkins.credentials.create.{field}" in workflow
    assert 'cmp "$render_dir/current.yaml" "$render_dir/v2.2.yaml"' in workflow
    assert 'cmp "$render_dir/current.yaml" "$render_dir/v2.1.yaml"' in workflow


def test_ldap_user_id_semantics_are_explicit() -> None:
    docs = "\n".join(
        path.read_text()
        for path in [
            ROOT / "README.md",
            ROOT / "ONBOARDING.md",
            CHART / "README.md",
            CHART / "values.yaml",
        ]
    )
    for phrase in ["LDAP", "User search filter", "{0}", "display name", "same Jenkins user"]:
        assert phrase in docs


def test_per_field_credential_refs_are_wired_and_validated() -> None:
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    deployment = (CHART / "templates/deployment.yaml").read_text()
    validate = (CHART / "templates/_validate.tpl").read_text()
    for helper in ["usernameSecretName", "usernameSecretKey", "tokenSecretName", "tokenSecretKey"]:
        assert helper in helpers
        assert helper in deployment
    assert "exactly one jenkins.credentials source may be enabled" in validate
    for conflict in ["existingSecret", "secretKeyRefs", "create", "externalSecret"]:
        assert conflict in validate


def test_minibridge_examples_demonstrate_both_policy_shapes() -> None:
    deny = yaml.safe_load((EXAMPLES / "minibridge.yaml").read_text())["minibridge"]
    assert deny["enabled"] is True
    assert deny["tools"]["deny"] == ["@destructive"]

    allow = yaml.safe_load((EXAMPLES / "minibridge-hardened.yaml").read_text())["minibridge"]
    assert allow["tools"]["allow"] == ["@read"]
    assert allow["basicAuth"]["enabled"] is True
    assert allow["tls"]["enabled"] is True
    # Secrets are referenced, never inlined.
    assert allow["basicAuth"]["existingSecret"]
    assert allow["tls"]["existingSecret"]


def test_minibridge_v080_environment_names_and_secret_pass_ref() -> None:
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    for env in [
        "MINIBRIDGE_POLICER_HTTP_URL",
        "MINIBRIDGE_POLICER_HTTP_BEARER_TOKEN",
        "MINIBRIDGE_POLICER_HTTP_CA",
        "MINIBRIDGE_MCP_USE_TEMPDIR",
    ]:
        assert env in helpers
    for obsolete in ["MINIBRIDGE_POLICER_URL", "MINIBRIDGE_POLICER_TOKEN", "MINIBRIDGE_POLICER_CA"]:
        assert f"name: {obsolete}\n" not in helpers
    assert "tls.pass.valueFrom.name" in helpers
    assert "tls.pass.valueFrom.key" in helpers


def test_raw_minibridge_manifests_support_read_only_root_filesystems() -> None:
    for path in [
        DEPLOY / "minibridge/kustomization.yaml",
        DEPLOY / "minibridge/standalone-deployment.yaml",
    ]:
        text = path.read_text()
        assert "/home/app/.config" in text
        assert "minibridge-config" in text
    assert "MINIBRIDGE_ENDPOINT_MCP=/mcp" in (DEPLOY / "minibridge/minibridge.env").read_text()
    assert (
        "MINIBRIDGE_ENDPOINT_MCP: /mcp"
        in (DEPLOY / "minibridge/standalone-deployment.yaml").read_text()
    )


def test_config_env_covers_every_supported_setting() -> None:
    """The raw manifests drifted from the app once; keep them in step."""
    import re

    cfg = set(re.findall(r"^([A-Z_]+)=", (DEPLOY / "base/config.env").read_text(), re.M))
    src = set(
        re.findall(
            r'alias="(MCP_[A-Z_]+|JENKINS_[A-Z_]+)"',
            (ROOT / "src/jenkins_mcp_server/config.py").read_text(),
        )
    )
    # Credentials and the optional CA bundle come from the Secret. File audit is
    # deliberately omitted so process logs stay the unbounded-safe default.
    from_secret = {"JENKINS_USERNAME", "JENKINS_TOKEN", "JENKINS_CA_BUNDLE"}
    # File health and rotation only have meaning when a path is set, so they are
    # unset here for the same reason.
    intentionally_unset = {
        "MCP_AUDIT_LOG_PATH",
        "MCP_AUDIT_REQUIRED_FOR_READINESS",
        "MCP_AUDIT_MAX_BYTES",
        "MCP_AUDIT_BACKUP_COUNT",
    }
    allowed_missing = from_secret | intentionally_unset
    assert (src - cfg) <= allowed_missing, f"config.env is missing {src - cfg - allowed_missing}"
    assert not (cfg - src), f"config.env sets unknown variables: {cfg - src}"


def test_compose_env_example_covers_every_supported_setting() -> None:
    """Docker users need every runtime knob, including optional commented ones."""
    src = set(
        re.findall(
            r'alias="(MCP_[A-Z_]+|JENKINS_[A-Z_]+)"',
            (ROOT / "src/jenkins_mcp_server/config.py").read_text(),
        )
    )
    documented = set(
        re.findall(r"^#? ?([A-Z][A-Z_]+)=", (ROOT / ".env.example").read_text(), re.M)
    )
    assert src <= documented, f".env.example is missing {src - documented}"


def test_minibridge_overlay_and_standalone_exist_and_parse() -> None:
    overlay = DEPLOY / "minibridge/kustomization.yaml"
    standalone = DEPLOY / "minibridge/standalone-deployment.yaml"
    assert yaml.safe_load(overlay.read_text())["kind"] == "Kustomization"
    docs = [d for d in yaml.safe_load_all(standalone.read_text()) if d]
    kinds = {d["kind"] for d in docs}
    assert {"Secret", "ConfigMap", "Deployment", "Service"} <= kinds
    config_map = next(d for d in docs if d["kind"] == "ConfigMap")
    assert config_map["data"]["JENKINS_MAX_CONCURRENCY"] == "10"
    dep = next(d for d in docs if d["kind"] == "Deployment")
    container = dep["spec"]["template"]["spec"]["containers"][0]
    # The minibridge image is required; the plain image has no minibridge binary.
    assert container["image"].endswith("-minibridge")
    # The entrypoint supplies the transport, so args must not override it.
    assert "args" not in container
    # minibridge health serves "/", not /healthz.
    assert container["livenessProbe"]["httpGet"]["path"] == "/"


def test_tailscale_directory_is_a_kustomization() -> None:
    """Overlays reference the directory; referencing files across dirs fails."""
    assert (DEPLOY / "tailscale/kustomization.yaml").is_file()
    production = yaml.safe_load((DEPLOY / "overlays/production/kustomization.yaml").read_text())
    assert "../../tailscale" in production["resources"]
    assert not any(r.endswith(".yaml") for r in production["resources"])
    tailscale = yaml.safe_load((DEPLOY / "tailscale/kustomization.yaml").read_text())
    assert "networkpolicy.yaml" in tailscale["resources"]


# --- Argo CD examples -----------------------------------------------------

ARGOCD = ROOT / "examples/argocd"


def applications():
    for f in sorted(ARGOCD.glob("application-*.yaml")):
        yield f, yaml.safe_load(f.read_text())


def test_argocd_applications_are_valid_and_pinned() -> None:
    files = list(applications())
    assert len(files) >= 3
    for f, app in files:
        assert app["kind"] == "Application", f
        rev = str(app["spec"]["source"]["targetRevision"])
        # A floating range would let a sync change versions without review.
        assert rev not in {"*", "HEAD", ""}, f"{f.name} has an unpinned revision"


def test_argocd_values_render_against_the_chart() -> None:
    """Catches an Application drifting from the chart's schema."""
    for f, app in applications():
        v = app["spec"]["source"]["helm"]["valuesObject"]
        try:
            jsonschema.validate(merge(values(), v), schema())
        except jsonschema.ValidationError as exc:
            raise AssertionError(f"{f.name}: {exc.message}") from exc


def test_argocd_applications_make_session_affinity_explicit() -> None:
    expected = {
        "sessionAffinity": "ClientIP",
        "sessionAffinityConfig": {"clientIP": {"timeoutSeconds": 600}},
    }
    for f, app in applications():
        configured = app["spec"]["source"]["helm"]["valuesObject"]["service"]["sessionAffinity"]
        assert configured == expected, f"{f.name} hides the production routing policy"


def test_argocd_applications_never_create_the_credentials_secret() -> None:
    for f, app in applications():
        creds = app["spec"]["source"]["helm"]["valuesObject"]["jenkins"]["credentials"]
        existing = creds["existingSecret"]
        assert existing["enabled"] is True, f"{f.name} must reference a Secret"
        assert existing["name"], f"{f.name} does not name a Secret"
        assert existing["usernameKey"], f"{f.name} does not name the user ID key"
        assert existing["tokenKey"], f"{f.name} does not name the API token key"


def test_argocd_applications_ignore_the_operator_rewritten_ingress_host() -> None:
    """Without this Argo CD reports permanent OutOfSync against Tailscale."""
    for f, app in applications():
        diffs = app["spec"].get("ignoreDifferences", [])
        assert any(d.get("kind") == "Ingress" for d in diffs), (
            f"{f.name} is missing the Ingress ignoreDifferences"
        )


def test_minibridge_argocd_example_enables_the_proxy() -> None:
    app = yaml.safe_load((ARGOCD / "application-minibridge.yaml").read_text())
    mb = app["spec"]["source"]["helm"]["valuesObject"]["minibridge"]
    assert mb["enabled"] is True
    assert mb["tools"]["deny"] == ["@destructive"]


def test_tailscale_guide_exists_and_is_linked() -> None:
    guide = ROOT / "docs/TAILSCALE.md"
    assert guide.is_file()
    text = guide.read_text()
    # The two things people get wrong, both must be covered.
    assert "login.tailscale.com/admin/dns" in text
    assert "machine name" in text
    assert "docs/TAILSCALE.md" in (ROOT / "README.md").read_text()
    assert "TAILSCALE.md" in (ROOT / "examples/README.md").read_text()


def test_client_docs_do_not_invent_a_universal_config_schema() -> None:
    active_docs = [
        ROOT / "README.md",
        ROOT / "ONBOARDING.md",
        ROOT / "docs/KUBERNETES_TAILSCALE_ARGOCD.md",
        CHART / "README.md",
        CHART / "templates/NOTES.txt",
    ]
    for path in active_docs:
        text = path.read_text()
        assert "mcp_servers:" not in text, path
        assert "Streamable HTTP" in text, path


def test_hermes_examples_use_the_current_http_mcp_schema() -> None:
    for path in [
        ROOT / "deploy/hermes/mcp-config.yaml",
        ROOT / "deploy/hermes/mcp-config-in-cluster.yaml",
    ]:
        config = yaml.safe_load(path.read_text())
        jenkins = config["mcp_servers"]["jenkins"]
        assert set(jenkins) == {"url", "timeout"}, path
        assert jenkins["url"].endswith("/mcp"), path
        assert jenkins["timeout"] == 60, path


# --- autoscaling and ingress ---------------------------------------------


def test_autoscaling_is_opt_in_and_templated() -> None:
    v = values()["autoscaling"]
    assert v["enabled"] is False
    assert v["minReplicas"] >= 1 and v["maxReplicas"] >= v["minReplicas"]
    assert (CHART / "templates/hpa.yaml").is_file()


def test_deployment_omits_replicas_when_autoscaling_is_enabled() -> None:
    """A declared replica count fights the HPA on every sync."""
    dep = (CHART / "templates/deployment.yaml").read_text()
    idx = dep.index("replicas:")
    window = dep[max(0, idx - 200) : idx]
    assert "not .Values.autoscaling.enabled" in window


def test_pdb_and_autoscaling_minimums_are_validated() -> None:
    validate = (CHART / "templates/_validate.tpl").read_text()
    assert "podDisruptionBudget.minAvailable" in validate
    assert "autoscaling.minReplicas" in validate
    # A percentage string cannot be compared numerically and must be skipped.
    assert 'kindIs "string"' in validate


def test_ingress_adapts_to_the_controller() -> None:
    ing = (CHART / "templates/ingress.yaml").read_text()
    # Tailscale takes the name from tls.hosts and wants no rule host; every
    # other controller needs one or the rule matches all hostnames.
    assert 'eq .Values.ingress.className "tailscale"' in ing
    assert "hostRule" in ing
    # General controllers need a TLS secret; Tailscale provisions its own.
    assert "secretName" in ing
    v = values()["ingress"]
    assert v["hostRule"] is None, "null lets the class decide"
    assert "tlsSecretName" in v


def test_rendered_templates_have_no_duplicate_yaml_keys() -> None:
    """The chart shipped a duplicated revisionHistoryLimit that lint missed."""
    import re

    dep = (CHART / "templates/deployment.yaml").read_text()
    top = [m.group(1) for m in re.finditer(r"^  ([a-zA-Z]+):", dep, re.M)]
    assert len(top) == len(set(top)), f"duplicate keys in deployment.yaml: {top}"


def test_helm_test_labels_do_not_emit_duplicate_component_keys() -> None:
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    hook = (CHART / "templates/tests/test-connection.yaml").read_text()
    assert 'define "jenkins-mcp-server.testLabels"' in helpers
    assert 'app.kubernetes.io/component: {{ "helm-test" | quote }}' in helpers
    assert 'include "jenkins-mcp-server.labels"' not in hook
    assert 'include "jenkins-mcp-server.testLabels"' in hook


def test_rendered_manifests_are_strictly_validated_in_ci_and_release() -> None:
    script = (ROOT / "scripts/validate_helm_renders.sh").read_text()
    for marker in ["kubeconform", "-strict", "examples/values/*.yaml"]:
        assert marker in script
    for workflow in ["ci.yml", "release.yml"]:
        text = (ROOT / ".github/workflows" / workflow).read_text()
        assert "KUBECONFORM_VERSION: v0.8.0" in text
        assert "./scripts/validate_helm_renders.sh" in text


def test_chart_owned_nested_values_reject_typos() -> None:
    sc = schema()
    for path in [
        ("image",),
        ("service",),
        ("ingress",),
        ("jenkins",),
        ("jenkins", "credentials"),
        ("mcp",),
        ("minibridge",),
        ("tailscale",),
        ("probes",),
        ("test",),
        ("audit",),
    ]:
        node = sc
        for key in path:
            node = node["properties"][key]
        assert node["additionalProperties"] is False, ".".join(path)

    invalid = values()
    invalid["probes"]["readiness"]["periodSecond"] = 10
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, sc)


def test_cross_field_validation_covers_credential_aliases_and_ranges() -> None:
    validate = (CHART / "templates/_validate.tpl").read_text()
    for marker in [
        "must not point at the same Secret key",
        "same usernameRemoteKey and tokenRemoteKey",
        "extraData[%d].secretKey",
        "minibridge.basicAuth must not reuse",
        "autoscaling.maxReplicas must be greater than or equal",
        "mcp.port and mcp.healthPort must be different",
    ]:
        assert marker in validate

    service_types = schema()["properties"]["service"]["properties"]["type"]["enum"]
    assert "ExternalName" not in service_types
    policies = schema()["properties"]["jenkins"]["properties"]["credentials"]["properties"][
        "externalSecret"
    ]["properties"]["creationPolicy"]["enum"]
    assert "CreateOrMerge" in policies


def test_helm_test_image_is_configurable() -> None:
    image = values()["test"]["image"]
    assert image == {
        "repository": "busybox",
        "tag": "1.37",
        "pullPolicy": "IfNotPresent",
    }
    hook = (CHART / "templates/tests/test-connection.yaml").read_text()
    assert ".Values.test.image.repository" in hook
    assert ".Values.test.image.tag" in hook
    assert ".Values.test.image.pullPolicy" in hook


# --- silently-ignored value combinations ---------------------------------


def test_tls_trust_dependencies_are_validated() -> None:
    validate = (CHART / "templates/_validate.tpl").read_text()
    # verifyTls false + a bundle previously turned verification back on.
    assert "jenkins.verifyTls is false but a CA bundle is configured" in validate
    # caBundlePath silently won over the mounted Secret.
    assert "caBundlePath" in validate and "existingSecret" in validate


def test_ca_bundle_is_optional_and_documented_as_such() -> None:
    v = values()["jenkins"]
    # The default must be empty: a publicly issued certificate needs nothing.
    assert v["caBundlePath"] == ""
    assert v["caBundle"]["existingSecret"] == ""
    assert v["verifyTls"] is True
    text = (CHART / "values.yaml").read_text()
    for phrase in ["OPTIONAL", "Let's Encrypt", "self-signed"]:
        assert phrase in text, f"values.yaml should explain {phrase!r}"


def test_minibridge_settings_are_not_silently_ignored() -> None:
    """Configuring guardrails while the proxy is off enforces nothing."""
    validate = (CHART / "templates/_validate.tpl").read_text()
    assert "minibridge.enabled is false" in validate
    for key in [
        "tools.deny",
        "tools.allow",
        "methodsDeny",
        "guardrails",
        "basicAuth.enabled",
        "tls.enabled",
    ]:
        assert key in validate, f"{key} is not covered by the ignored-settings guard"


def test_ingress_tls_secret_requires_tls() -> None:
    validate = (CHART / "templates/_validate.tpl").read_text()
    assert "ingress.tlsSecretName is set but ingress.tls is false" in validate


# --- port consistency and schema completeness ----------------------------


def test_service_and_networkpolicy_follow_the_effective_ports() -> None:
    """minibridge moves the health port; these must not stay on the raw value."""
    for name in ["service.yaml", "networkpolicy.yaml"]:
        text = (CHART / "templates" / name).read_text()
        assert ".Values.mcp.healthPort" not in text, f"{name} hardcodes the health port"
        assert ".Values.mcp.port" not in text, f"{name} hardcodes the mcp port"
        assert "jenkins-mcp-server." in text and "Port" in text


def test_pdb_guard_covers_static_replicas_and_autoscaling() -> None:
    validate = (CHART / "templates/_validate.tpl").read_text()
    assert "replicaCount" in validate
    assert "autoscaling.minReplicas" in validate
    assert "node drains block" in validate


def test_audit_pvc_claim_name_key_exists_for_required_to_work() -> None:
    """Without the key, .claimName dereferences nil before `required` fires."""
    storage = values()["audit"]["storage"]
    assert "persistentVolumeClaim" in storage
    assert storage["persistentVolumeClaim"]["claimName"] == ""


def test_schema_covers_every_value_and_rejects_typos() -> None:
    sc = schema()
    unvalidated = sorted(set(values()) - set(sc["properties"]))
    assert not unvalidated, f"values with no schema entry: {unvalidated}"
    # Otherwise replicaCoun: 3 is silently ignored.
    assert sc.get("additionalProperties") is False
    # Helm injects this for subcharts; strict validation must still allow it.
    assert "global" in sc["properties"]


def test_notes_describe_the_actual_deployment() -> None:
    notes = (CHART / "templates/NOTES.txt").read_text()
    # Must not assume Tailscale, and must reflect the real enforcement state.
    assert 'eq .Values.ingress.className "tailscale"' in notes
    assert "policer.enforce" in notes
    assert "minibridge.enabled" in notes
    assert "verifyTls" in notes
    assert "NetworkPolicy is disabled" in notes
    assert 'ne .Values.service.type "ClusterIP"' in notes


def test_health_port_exposure_is_configurable() -> None:
    """/readyz reports config and transport state; do not force it onto a LoadBalancer."""
    assert values()["service"]["exposeHealthPort"] is True
    svc = (CHART / "templates/service.yaml").read_text()
    assert ".Values.service.exposeHealthPort" in svc


def test_service_keeps_streamable_http_sessions_on_one_pod() -> None:
    """Minibridge session state is process-local, so multi-replica traffic is sticky."""
    affinity = values()["service"]["sessionAffinity"]
    assert affinity == {
        "sessionAffinity": "ClientIP",
        "sessionAffinityConfig": {"clientIP": {"timeoutSeconds": 600}},
    }
    svc = (CHART / "templates/service.yaml").read_text()
    assert ".Values.service.sessionAffinity" in svc
    assert "toYaml" in svc
    assert "sessionAffinity" in schema()["properties"]["service"]["properties"]
    disabled = values()
    disabled["service"]["sessionAffinity"] = None
    jsonschema.validate(disabled, schema())


def test_production_values_make_session_affinity_explicit() -> None:
    expected = {
        "sessionAffinity": "ClientIP",
        "sessionAffinityConfig": {"clientIP": {"timeoutSeconds": 600}},
    }
    for name in [
        "tailscale-production.yaml",
        "generic-ingress-hpa.yaml",
        "minibridge.yaml",
        "minibridge-hardened.yaml",
    ]:
        configured = yaml.safe_load((EXAMPLES / name).read_text())["service"]["sessionAffinity"]
        assert configured == expected, name


def test_component_label_does_not_change_immutable_selectors() -> None:
    """Add workload identity without making existing Deployment selectors drift."""
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    assert "app.kubernetes.io/component: mcp-server" in helpers
    selector = helpers.split('{{- define "jenkins-mcp-server.selectorLabels" -}}', 1)[1].split(
        "{{- end }}", 1
    )[0]
    assert "app.kubernetes.io/component" not in selector
    deployment = (CHART / "templates/deployment.yaml").read_text()
    assert "app.kubernetes.io/component: mcp-server" in deployment
    assert values()["podLabels"] == {}


def test_jenkins_compatibility_is_documented() -> None:
    doc = ROOT / "docs/JENKINS_COMPATIBILITY.md"
    assert doc.is_file()
    text = doc.read_text()
    for topic in [
        "lts-jdk21",
        "cloudbees-folder",
        "workflow-multibranch",
        "API token",
        "Job/Delete",
    ]:
        assert topic in text, f"compatibility doc should cover {topic}"
    assert "JENKINS_COMPATIBILITY.md" in (ROOT / "README.md").read_text()


def test_documented_endpoints_match_the_client() -> None:
    """The compatibility table must not drift from the code."""

    client = (ROOT / "src/jenkins_mcp_server/client.py").read_text()
    doc = (ROOT / "docs/JENKINS_COMPATIBILITY.md").read_text()
    for endpoint in [
        "crumbIssuer",
        "createItem",
        "doDelete",
        "toggleOffline",
        "progressiveText",
        "cancelItem",
        "config.xml",
    ]:
        assert endpoint in client, f"{endpoint} no longer used by the client"
        assert endpoint in doc, f"{endpoint} missing from the compatibility doc"


def test_autoscaling_argocd_example_ignores_replicas() -> None:
    """Otherwise Argo CD fights the HPA and reports permanent OutOfSync."""
    app = yaml.safe_load((ARGOCD / "application-hpa-generic.yaml").read_text())
    v = app["spec"]["source"]["helm"]["valuesObject"]
    assert v["autoscaling"]["enabled"] is True
    diffs = app["spec"]["ignoreDifferences"]
    assert any(
        d.get("kind") == "Deployment" and "/spec/replicas" in d.get("jsonPointers", [])
        for d in diffs
    )


def test_compatibility_matrix_lists_concrete_versions() -> None:
    """The README must state which cores were actually tested, not just 'LTS'."""
    readme = (ROOT / "README.md").read_text()
    section = _section(readme, "Jenkins compatibility", "Capabilities")
    # The versions actually exercised end to end must be named.
    for version in ["2.555", "2.541.3", "2.504.3", "2.504.1"]:
        assert version in section, f"{version} missing from the compatibility matrix"
    # Untested and unsupported releases must be distinguished from verified ones.
    assert "not covered by ci" in section.lower()
    assert "unsupported" in section.lower()
    # Status must be scannable at a glance.
    assert section.count("✅") >= 4
    # 2.50 is not a published Docker tag and must not appear as a version.
    assert "| `2.50` |" not in readme


def test_plugin_blockers_are_documented() -> None:
    doc = (ROOT / "docs/JENKINS_COMPATIBILITY.md").read_text()
    for blocker in [
        "cloudbees-folder",
        "Strict Crumb Issuer",
        "Job/ExtendedRead",
        "buildWithParameters",
        "script approval",
        "path prefix",
    ]:
        assert blocker in doc, f"{blocker} should be covered as a known blocker"


def test_chart_readme_documents_every_value() -> None:
    """The chart README had drifted to covering 7 of 32 top-level values."""
    readme = (CHART / "README.md").read_text()
    undocumented = [k for k in sorted(values()) if k not in readme]
    assert not undocumented, f"chart README omits: {undocumented}"


def test_no_stale_version_pins_anywhere() -> None:
    """Every declared pin and the repository-wide stale scan must pass."""
    import subprocess

    result = subprocess.run(
        ["python3", str(ROOT / "scripts/check_version.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_version_bump_updates_every_pin_and_detects_future_unmanaged_files(
    tmp_path,
) -> None:
    """A new manifest cannot freeze without either being updated or failing CI."""
    import shutil
    import subprocess
    import sys

    copied = tmp_path / "repo"
    shutil.copytree(
        ROOT,
        copied,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "pytest-of-root",
            "__pycache__",
            "build",
            "dist",
        ),
    )
    # Model a maintainer completing the fresh Unreleased template before the
    # next release. Production deliberately rejects untouched placeholders.
    changelog_path = copied / "CHANGELOG.md"
    changelog_path.write_text(
        changelog_path.read_text(encoding="utf-8").replace(
            "- None yet.", "- Reviewed release detail.", 8
        ),
        encoding="utf-8",
    )
    bump = subprocess.run(
        ["make", "version", "VERSION=9.8.7"],
        cwd=copied,
        capture_output=True,
        text=True,
    )
    assert bump.returncode == 0, bump.stderr
    changelog = (copied / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [9.8.7] - " in changelog
    check = subprocess.run(
        [sys.executable, "scripts/check_version.py"],
        cwd=copied,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr
    assert "21 managed version pins in 17 files" in check.stdout

    future = copied / "future/deployment.md"
    future.parent.mkdir()
    stale_version = ".".join(["1", "19", "0"])
    future.write_text(
        f"image: ghcr.io/grglzrv/jenkins-mcp-server:{stale_version}\n",
        encoding="utf-8",
    )
    stale = subprocess.run(
        [sys.executable, "scripts/check_version.py"],
        cwd=copied,
        capture_output=True,
        text=True,
    )
    assert stale.returncode != 0
    assert f"future/deployment.md: pins {stale_version}" in stale.stderr


# --- cluster smoke test ---------------------------------------------------


def test_smoke_workflow_and_values_exist() -> None:
    wf = ROOT / ".github/workflows/chart-smoke.yml"
    vals = ROOT / ".github/smoke-values.yaml"
    assert wf.is_file() and vals.is_file()
    text = wf.read_text()
    # The value of a cluster test is what only an API server can tell you.
    for step in ["helm install", "helm test", "helm upgrade", "helm uninstall", "rollout status"]:
        assert step in text, f"smoke test should cover {step}"


def test_smoke_test_is_reused_not_duplicated() -> None:
    """CI and release must call the same workflow rather than copy the steps."""
    for name in ["ci.yml", "release.yml"]:
        text = (ROOT / ".github/workflows" / name).read_text()
        assert "uses: ./.github/workflows/chart-smoke.yml" in text, name
        assert "get.k3s.io" not in text, f"{name} duplicates the smoke steps"


def test_release_is_gated_on_the_smoke_test() -> None:
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert "chart-smoke" in release["jobs"]
    assert "chart-smoke" in release["jobs"]["github-release"]["needs"]
    # A published image tag the chart references must also exist.
    assert "minibridge-image" in release["jobs"]


def test_version_changes_publish_automatically_and_idempotently() -> None:
    text = (ROOT / ".github/workflows/release.yml").read_text()
    assert "workflow_call:" in text
    assert "branches:\n      - main" in text
    assert "paths:\n      - VERSION" in text
    assert "releases/tags/${TAG}" in text
    assert "needs.validate.outputs.publish == 'true'" in text
    assert '--target "${RELEASE_TARGET}"' in text
    # Branch and reusable runs have no tag event for metadata-action to infer.
    for pattern in ["{{version}}", "{{major}}.{{minor}}", "{{major}}"]:
        assert f"pattern={pattern},value=${{{{ needs.validate.outputs.version }}}}" in text


def test_release_backfill_is_ordered_and_uses_exact_sources() -> None:
    backfill = yaml.safe_load((ROOT / ".github/workflows/backfill-releases.yml").read_text())
    jobs = backfill["jobs"]
    expected = {
        "release-1-18": "1a6db437cefc08530d2b79fa1190d7b732112dc8",
        "release-1-19": "b457b6ebf741df4763a93b8046f59ff7726daff0",
        "release-1-20": "9b0cb04bad35a9f2c9526e4f247ae58caadb8356",
    }
    for job, source_ref in expected.items():
        assert jobs[job]["uses"] == "./.github/workflows/release.yml"
        assert jobs[job]["with"]["source_ref"] == source_ref
    assert jobs["release-1-19"]["needs"] == "release-1-18"
    assert jobs["release-1-20"]["needs"] == "release-1-19"


def test_historical_release_smoke_checks_out_release_source() -> None:
    text = (ROOT / ".github/workflows/chart-smoke.yml").read_text()
    assert "source_ref:" in text
    # Every reusable smoke job, including credential-source reconciliation,
    # must test the requested historical release rather than current main.
    assert text.count("ref: ${{ inputs.source_ref || github.ref }}") == 4


def test_smoke_values_disable_what_the_cluster_lacks() -> None:
    v = yaml.safe_load((ROOT / ".github/smoke-values.yaml").read_text())
    assert v["ingress"]["enabled"] is False
    assert v["tailscale"]["egress"]["enabled"] is False
    # The NetworkPolicy must stay on: the helm test reaching the Service through
    # it is a large part of what the smoke test proves.
    assert v["networkPolicy"]["enabled"] is True
    for path in sorted((ROOT / ".github").glob("smoke-values*.yaml")):
        smoke_values = yaml.safe_load(path.read_text())
        assert smoke_values["mcp"]["maxResponseBytes"] == 10_000_000, path.name
    workflow = (ROOT / ".github/workflows/chart-smoke.yml").read_text()
    assert 'os.environ["MCP_MAX_RESPONSE_BYTES"] == "10000000"' in workflow


def test_chart_readme_documents_kubernetes_support() -> None:
    readme = (CHART / "README.md").read_text()
    assert "kubeVersion" in readme
    for minor in ["1.36", "1.35", "1.34", "1.33"]:
        assert minor in readme, f"Kubernetes {minor} missing from the matrix"
    for api in ["policy/v1", "autoscaling/v2", "networking.k8s.io/v1"]:
        assert api in readme, f"{api} missing from the API version table"


def test_helm_test_pod_uses_the_effective_port_and_path() -> None:
    """Use the effective endpoint and tolerate Service propagation latency."""
    text = (CHART / "templates/tests/test-connection.yaml").read_text()
    assert ".Values.mcp.healthPort" not in text
    assert "jenkins-mcp-server.healthPort" in text
    assert "jenkins-mcp-server.readyPath" in text
    # No test pod when the health port is not published on the Service.
    assert ".Values.service.exposeHealthPort" in text
    # A single request races endpoint propagation after the Deployment becomes
    # Available. The hook must retry, but still fail within Helm's test timeout.
    assert 'command: ["sh", "-ec"]' in text
    assert 'while [ "$attempt" -le 30 ]' in text
    assert "sleep 2" in text
    assert "--timeout=3" in text
    assert "--tries=1" in text


def test_numeric_env_values_are_not_rendered_in_scientific_notation() -> None:
    """Helm renders 1000000 as "1e+06" without an int cast.

    The server rejects that at startup, so every pod crash-looped on default
    values. Only a real install surfaced it; rendering looked fine.
    """
    cm = (CHART / "templates/configmap.yaml").read_text()
    for key in [
        "maxResponseBytes",
        "maxLogBytes",
        "mcp.port",
        "healthPort",
        "maxConcurrency",
        "maxRetries",
    ]:
        # Assignment lines only. A comment mentioning the value is not a render.
        line = next(
            ln
            for ln in cm.splitlines()
            if key in ln and not ln.lstrip().startswith("#") and "{{" in ln
        )
        assert "| int |" in line or "printf" in line, f"{key} needs an int cast: {line}"


def test_settings_reject_scientific_notation_so_the_cast_matters() -> None:
    """Guards the assumption behind the fix above."""
    from jenkins_mcp_server.config import Settings

    base = dict(JENKINS_URL="https://j.test", JENKINS_USERNAME="u", JENKINS_TOKEN="t")
    assert Settings(**base, MCP_MAX_LOG_BYTES="1000000").max_log_bytes == 1000000
    assert Settings(**base, MCP_MAX_RESPONSE_BYTES="10000000").max_response_bytes == 10000000
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        Settings(**base, MCP_MAX_LOG_BYTES="1e+06")
    with pytest.raises(pydantic.ValidationError):
        Settings(**base, MCP_MAX_RESPONSE_BYTES="1e+07")


# --- neutral defaults -----------------------------------------------------


def test_chart_defaults_assume_nothing_about_the_cluster() -> None:
    """No ingress controller, no Tailscale, no hardcoded hostnames."""
    v = values()
    assert v["ingress"]["enabled"] is False
    assert v["ingress"]["className"] == "", "must not hardcode an IngressClass"
    assert v["ingress"]["annotations"] == {}, "annotations are controller-specific"
    assert v["ingress"]["hostname"] == ""
    assert v["tailscale"]["enabled"] is False, "Tailscale must be opt-in"
    assert v["tailscale"]["egress"]["enabled"] is False
    assert v["jenkins"]["url"] == "", "no default Jenkins URL"
    assert v["tailscale"]["egress"]["tailnetFQDN"] == ""


def test_no_tailnet_hostnames_leak_into_chart_defaults() -> None:
    """The chart must not ship a tailnet hostname as a default value.

    Matches a complete MagicDNS name rather than the substring "ts.net", which
    also hits unrelated words and reads as loose host matching.
    """
    import re

    # Anchored on both sides so it matches a whole hostname. Testing for the
    # substring "ts.net" would also match ts.network and ts.net.evil.test.
    tailnet_host = re.compile(
        r"(?<![A-Za-z0-9.-])([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.)?ts\.net(?![A-Za-z0-9.-])"
    )
    text = (CHART / "values.yaml").read_text()
    defaults = [
        ln for ln in text.splitlines() if tailnet_host.search(ln) and not ln.strip().startswith("#")
    ]
    assert not defaults, f"tailnet hostnames in non-comment defaults: {defaults}"


def test_required_values_are_enforced() -> None:
    validate = (CHART / "templates/_validate.tpl").read_text()
    assert "jenkins.url is required" in validate
    # A Tailscale sub-feature configured while the integration is off would
    # render nothing at all.
    assert "tailscale.enabled is false" in validate
    assert "tailscale.egress.tailnetFQDN is required" in validate


def test_ingress_class_is_optional() -> None:
    """An empty class lets the cluster's default IngressClass apply."""
    ing = (CHART / "templates/ingress.yaml").read_text()
    assert "with .Values.ingress.className" in ing


def test_tailscale_resources_require_the_master_switch() -> None:
    for name in [
        "tailscale-egress-service.yaml",
        "tailscale-dnsconfig.yaml",
        "tailscale-proxygroups.yaml",
    ]:
        text = (CHART / "templates" / name).read_text()
        assert "and .Values.tailscale.enabled" in text, name


def test_chart_readme_reflects_neutral_defaults() -> None:
    """It described a Tailscale install as though it were the default."""
    readme = (CHART / "README.md").read_text()
    # A quick start that works on a plain cluster.
    assert "## Quick start" in readme
    assert "jenkins.url=https://jenkins.example.com" in readme
    assert "jenkins.credentials.existingSecret.enabled=true" in readme
    # Tailscale must be presented as opt-in, not assumed.
    assert "Tailscale integration (optional)" in readme
    assert "tailscale:\n  enabled: true" in readme
    # The in-cluster endpoint must be documented, not only an ingress hostname.
    assert "svc.cluster.local:8000/mcp" in readme


def test_main_helm_quick_start_is_for_external_jenkins() -> None:
    readme = (ROOT / "README.md").read_text()
    install = _section(readme, "Helm installation", "Connecting a client")
    assert "examples/values/existing-secret.yaml" in install
    assert "jenkins.url=https://jenkins.example.com" in install
    assert "tailscale-production.yaml" not in install


def test_chart_readme_service_name_matches_the_template() -> None:
    """The documented in-cluster URL must be the name the chart renders."""
    readme = (CHART / "README.md").read_text()
    # helm fullname for release jenkins-mcp and chart jenkins-mcp-server.
    assert "jenkins-mcp-jenkins-mcp-server" in readme


def test_shipped_changes_require_a_version_bump() -> None:
    """A chart or source change without a bump is never published.

    The release workflow triggers on a VERSION change, and the chart's image
    tag follows appVersion, so an unbumped change silently ships nothing.
    """
    script = ROOT / "scripts/check_release_bump.py"
    assert script.is_file()
    body = script.read_text()
    for path in [
        "src/",
        "charts/jenkins-mcp-server/",
        "docker/",
        "deploy/",
        "examples/argocd/",
        "examples/values/",
    ]:
        assert path in body, f"{path} should require a bump"
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert "version-bump" in ci["jobs"]


def test_release_reruns_refuse_a_tag_from_another_commit() -> None:
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert "git ls-remote origin" in release
    assert "already points to" in release
    assert "GitHub Release but no corresponding Git tag" in release
    assert "group: release-${{ github.repository }}" in release
    assert "--assert-newer" in release


def test_release_smoke_gates_every_external_publish() -> None:
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    for job in ["image", "minibridge-image", "helm"]:
        assert "chart-smoke" in release["jobs"][job]["needs"]


def test_python_metadata_check_ignores_packaged_helm_chart() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "twine check dist/*.whl dist/*.tar.gz" in makefile
    assert "twine check dist/*\n" not in makefile


def test_chart_version_and_app_version_move_together() -> None:
    """Deliberate coupling: the chart is only ever tested against its own image."""
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    version = (ROOT / "VERSION").read_text().strip()
    assert chart["version"] == version
    assert str(chart["appVersion"]).strip('"') == version
    # image.tag empty means the chart uses appVersion, so the pair cannot drift.
    assert values()["image"]["tag"] == ""


# --- agent onboarding -----------------------------------------------------


def test_onboarding_exists_and_is_linked_from_the_readme() -> None:
    assert (ROOT / "ONBOARDING.md").is_file()
    readme = (ROOT / "README.md").read_text()
    assert "ONBOARDING.md" in readme
    assert "If you are an AI agent" in readme


def test_onboarding_names_match_what_the_chart_renders() -> None:
    """Resource names in the instructions must be the ones Helm produces."""
    onboarding = (ROOT / "ONBOARDING.md").read_text()
    # Release jenkins-mcp of chart jenkins-mcp-server.
    assert "jenkins-mcp-jenkins-mcp-server" in onboarding
    assert "jenkins-mcp-secrets" in onboarding
    # The documented port and path must match the chart defaults.
    assert f":{values()['service']['port']}" in onboarding
    assert values()["mcp"]["path"] in onboarding


def test_onboarding_only_references_files_that_exist() -> None:
    import re

    onboarding = (ROOT / "ONBOARDING.md").read_text()
    targets = [
        link
        for link in re.findall(r"\]\(([^)#][^)]*)\)", onboarding)
        if not link.startswith(("http", "#"))
    ]
    missing = [t for t in targets if not (ROOT / t).exists()]
    assert not missing, f"ONBOARDING.md links to missing files: {missing}"


def test_onboarding_states_the_safety_rules_for_an_agent() -> None:
    """These instructions are executed by an agent with cluster access."""
    onboarding = (ROOT / "ONBOARDING.md").read_text().lower()
    for rule in [
        "never invent",
        "ask before anything that changes state",
        "do not disable tls verification",
        "do not write secrets into values files",
    ]:
        assert rule in onboarding, f"missing agent safety rule: {rule}"


def test_readme_headline_claims_match_reality() -> None:
    """The opening is a promise; keep it tied to what CI actually proves."""
    import re

    readme = (ROOT / "README.md").read_text()
    intro = " ".join(readme.split("## 🚀 Two ways to install")[0].split())

    # Tool count.
    server = (ROOT / "src/jenkins_mcp_server/server.py").read_text()
    tools = len(re.findall(r"@mcp\.tool\(\)", server))
    assert f"{tools} Jenkins tools" in intro, f"intro should say {tools} tools"

    # Count rows in the compatibility table only. Counting the whole file also
    # matches headings that happen to contain the same marker.
    table = _section(readme, "Jenkins compatibility", "Capabilities")
    verified = len([ln for ln in table.splitlines() if ln.startswith("|") and "✅ Verified" in ln])
    # The claim moved into the security section; assert it wherever it is made.
    assert "four Jenkins LTS lines" in " ".join(readme.split()), (
        "README no longer states how many Jenkins lines are verified"
    )
    assert verified == 4, f"claim says four verified lines, table shows {verified}"

    # Kubernetes versions actually installed by the smoke matrix.
    smoke = (ROOT / ".github/workflows/chart-smoke.yml").read_text()
    minors = {m for m in re.findall(r"v(1\.\d+)\.\d+\+k3s", smoke)}
    assert "four Kubernetes versions" in " ".join(readme.split())
    assert len(minors) == 4, f"smoke matrix covers {sorted(minors)}"

    # The defaults the intro promises are opt-in.
    mcp = values()["mcp"]
    assert mcp["allowJobDelete"] is False
    assert mcp["allowAdminRequest"] is False


def test_readme_anchor_links_resolve() -> None:
    """An emoji heading gains a leading hyphen in GitHub's anchor.

    `## 🚀 Two ways to install` renders as `#-two-ways-to-install`, so a link
    written as `#two-ways-to-install` silently goes nowhere.
    """
    import re

    readme = (ROOT / "README.md").read_text()

    def slug(heading: str) -> str:
        cleaned = re.sub(r"[^\w\s-]", "", heading.lower())
        return "#" + re.sub(r"\s+", "-", cleaned).strip()

    anchors = {slug(h) for h in re.findall(r"^#{2,3} (.+)$", readme, re.M)}
    used = set(re.findall(r"\]\((#[^)]+)\)", readme))
    assert used <= anchors, f"broken anchor links: {sorted(used - anchors)}"


def test_documented_transports_match_the_code() -> None:
    """The README advertised only one of the two transports the server has."""
    import re

    readme = (ROOT / "README.md").read_text()
    config = (ROOT / "src/jenkins_mcp_server/config.py").read_text()

    supported = set(
        re.findall(r'"([a-z-]+)"', re.search(r"transport: Literal\[([^\]]+)\]", config).group(1))
    )
    section = _section(readme, "Connecting a client", "Security and guardrails")
    for transport in supported:
        assert f"`{transport}`" in section, f"{transport} is supported but undocumented"

    # SSE as a separate transport is deprecated; do not advertise it as offered.
    assert "deprecated in the 2025-03-26 revision" in section


def test_minibridge_exposes_streamable_http_with_a_private_stdio_child() -> None:
    """Client and child transports must not be conflated in rendered config."""
    configmap = (CHART / "templates/configmap.yaml").read_text()
    assert "not .Values.minibridge.enabled" in configmap
    assert 'MCP_TRANSPORT: "stdio"' not in configmap
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    assert "MINIBRIDGE_ENDPOINT_MCP" in helpers
    assert ".Values.mcp.path" in helpers
    entrypoint = (ROOT / "docker/entrypoint.sh").read_text()
    assert "--transport stdio" in entrypoint
    assert "mcp-proxy" not in entrypoint

    # Both real smoke clients speak Streamable HTTP through Minibridge. If the
    # public frontend regresses to stdio, CI cannot initialize either session.
    for probe in ["minibridge_probe.py", "minibridge_all_tools.py"]:
        text = (ROOT / "integration" / probe).read_text()
        assert "streamable_http_client" in text

    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "Render Minibridge Streamable HTTP with a custom endpoint" in workflow
    assert "--set mcp.path=/custom-mcp" in workflow
    assert "MINIBRIDGE_ENDPOINT_MCP" in workflow

    jenkins_fixture = (ROOT / "integration/jenkins/Dockerfile").read_text()
    assert "install_with_retry" in jenkins_fixture
    assert 'if [ "$attempt" -ge 3 ]' in jenkins_fixture


def test_minibridge_topology_diagram_is_published_with_both_install_paths() -> None:
    markers = [
        "```mermaid",
        "flowchart LR",
        'Client -->|"Streamable HTTP /mcp"| MiniBridge',
        'MiniBridge["Minibridge AIO"] -->|"private stdio pipe"|',
        'Server -->|"HTTPS API"| Jenkins',
    ]
    for path in [
        ROOT / "README.md",
        CHART / "README.md",
        ROOT / "docs/KUBERNETES_TAILSCALE_ARGOCD.md",
    ]:
        text = path.read_text()
        for marker in markers:
            assert marker in text, f"{path.relative_to(ROOT)} is missing {marker}"


def test_edge_and_release_images_cover_the_same_platforms() -> None:
    """An edge tag that drops an architecture is not a preview of the release."""
    import re

    edge = (ROOT / ".github/workflows/publish-edge.yml").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()
    for name, text in (("publish-edge", edge), ("release", release)):
        platforms = set(re.findall(r"platforms:\s*(\S+)", text))
        assert platforms == {"linux/amd64,linux/arm64"}, (
            f"{name} builds inconsistent platforms: {sorted(platforms)}"
        )
    # Both workflows must build the minibridge variant, not only the default.
    for name, text in (("publish-edge", edge), ("release", release)):
        assert "docker/Dockerfile.minibridge" in text, name


def test_documented_values_blocks_match_the_current_schema() -> None:
    """A values example in prose is as breaking as one in examples/.

    The 2.0.0 restructure changed the shape of jenkins.credentials, and a
    documented block that still shows the old form sends people to a render
    failure.
    """
    import re

    for doc in ["ONBOARDING.md", "README.md", "charts/jenkins-mcp-server/README.md"]:
        text = (ROOT / doc).read_text()
        for block in re.findall(r"```yaml\n(.*?)```", text, re.S):
            parsed = yaml.safe_load(block)
            if not isinstance(parsed, dict):
                continue
            creds = (parsed.get("jenkins") or {}).get("credentials")
            if not creds:
                continue
            # Every source is an object with an enabled flag as of 2.0.0.
            for name, value in creds.items():
                assert isinstance(value, dict), (
                    f"{doc}: jenkins.credentials.{name} must be an object, got {value!r}"
                )
                assert "enabled" in value, f"{doc}: jenkins.credentials.{name} is missing 'enabled'"
            assert "externalSecret" not in parsed, (
                f"{doc}: externalSecret moved under jenkins.credentials in 2.0.0"
            )
            existing = creds.get("existingSecret", {})
            if existing.get("enabled"):
                for key in ["name", "usernameKey", "tokenKey"]:
                    assert existing.get(key), (
                        f"{doc}: enabled existingSecret must explicitly set {key}"
                    )


def test_extra_env_guard_rejects_chart_owned_names_in_any_case() -> None:
    """The server reads settings case-sensitively, so a lowercase spelling of a
    chart-owned name would be accepted here and then do nothing at runtime.
    Rejecting it reports the mistake instead of shipping an inert value.
    """
    validate = (CHART / "templates/_validate.tpl").read_text()
    assert "upper $name" in validate
    assert "in any capitalisation" in validate


def test_extra_env_guard_covers_every_app_and_minibridge_variable() -> None:
    """A new chart-owned env name must not silently escape the extraEnv guard.

    The chart templates define ownership: an entrypoint-only variable remains a
    valid ``mcp.extraEnv`` extension until the chart also emits it.  Keep the
    exact guard in lockstep with unprefixed variables emitted by the chart, and
    separately prove that chart-owned policy inputs reach the entrypoint
    translation layer.
    """
    config = (ROOT / "src/jenkins_mcp_server/config.py").read_text()
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    validate = (CHART / "templates/_validate.tpl").read_text()

    aliases = set(re.findall(r'alias="([A-Z][A-Z0-9_]*)"', config))
    minibridge_names = set(
        re.findall(r"^- name: ([A-Z][A-Z0-9_]*)$", helpers, flags=re.MULTILINE)
    )
    exact_match = re.search(r"\$extraEnvExact := list ([^\n]+)", validate)
    assert exact_match, "the exact chart-owned environment list is missing"
    exact = set(re.findall(r'"([A-Z][A-Z0-9_]*)"', exact_match.group(1)))

    def guarded(name: str) -> bool:
        return name.startswith(("JENKINS_", "MCP_", "MINIBRIDGE_")) or name in exact

    owned = aliases | minibridge_names
    unguarded = sorted(name for name in owned if not guarded(name))
    assert not unguarded, f"chart-owned environment names escape mcp.extraEnv: {unguarded}"

    unprefixed_chart_names = {
        name
        for name in minibridge_names
        if not name.startswith(("JENKINS_", "MCP_", "MINIBRIDGE_"))
    }
    assert exact == unprefixed_chart_names, (
        "the exact extraEnv guard must equal the chart's unprefixed environment names; "
        f"missing={sorted(unprefixed_chart_names - exact)}, "
        f"stale={sorted(exact - unprefixed_chart_names)}"
    )

    entrypoint = (ROOT / "docker/entrypoint.sh").read_text()
    translated_policy_inputs = set(
        re.findall(
            r'export REGO_POLICY_RUNTIME_[A-Z0-9_]+="\$\{([A-Z][A-Z0-9_]*):-\}"',
            entrypoint,
        )
    )
    # Minibridge consumes OTEL_EXPORTER_OTLP_ENDPOINT directly. The other
    # unprefixed chart values are compatibility inputs translated for Rego.
    expected_translations = unprefixed_chart_names - {"OTEL_EXPORTER_OTLP_ENDPOINT"}
    assert expected_translations <= translated_policy_inputs, (
        "chart-owned policy values are not translated by docker/entrypoint.sh: "
        f"{sorted(expected_translations - translated_policy_inputs)}"
    )


def test_settings_are_case_sensitive() -> None:
    """Guards the source-side half of the same defence."""
    config = (ROOT / "src/jenkins_mcp_server/config.py").read_text()
    assert "case_sensitive=True" in config
