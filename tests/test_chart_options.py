"""Chart wiring tests for destructive-action flags and ExternalSecret options."""

import json
from pathlib import Path

import jsonschema
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


def test_destructive_flags_exist_with_safe_defaults() -> None:
    mcp = values()["mcp"]
    for key in DESTRUCTIVE_VALUES:
        assert key in mcp, f"{key} missing from values.yaml"
    # Deleting a job is irreversible, so it must not be on by default.
    assert mcp["allowJobDelete"] is False
    assert mcp["allowDestructive"] is True


def test_destructive_flags_are_passed_to_the_container() -> None:
    configmap = (CHART / "templates/configmap.yaml").read_text()
    for env in DESTRUCTIVE_ENV:
        assert env in configmap, f"{env} not wired into the ConfigMap"


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


# --- ExternalSecret -------------------------------------------------------


def test_external_secret_and_helm_created_secret_are_mutually_exclusive() -> None:
    template = (CHART / "templates/externalsecret.yaml").read_text()
    assert "fail" in template
    assert ".Values.jenkins.credentials.create" in template


def test_external_secret_exposes_creation_options() -> None:
    es = values()["externalSecret"]
    for key in [
        "apiVersion",
        "creationPolicy",
        "deletionPolicy",
        "secretStore",
        "usernameRemoteProperty",
        "tokenRemoteProperty",
    ]:
        assert key in es, f"{key} missing from externalSecret values"
    assert es["secretStore"]["create"] is False
    assert es["enabled"] is False


def test_optional_secret_store_creation_is_templated() -> None:
    template = (CHART / "templates/externalsecret.yaml").read_text()
    assert ".Values.externalSecret.secretStore.create" in template
    assert "provider" in template
    # The provider must be required when creating a store, or ESO gets a no-op.
    assert "required" in template


def test_values_and_production_example_still_match_schema() -> None:
    base = values()
    jsonschema.validate(base, schema())
    production = yaml.safe_load(
        (ROOT / "examples/values/tailscale-production.yaml").read_text()
    )

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
    gcpsm = gcp_example()["externalSecret"]["secretStore"]["provider"]["gcpsm"]
    assert "projectID" in gcpsm
    wi = gcpsm["auth"]["workloadIdentity"]
    for field in ["clusterProjectID", "clusterName", "clusterLocation", "serviceAccountRef"]:
        assert field in wi, f"gcpsm workloadIdentity.{field} missing"


def test_gcp_cluster_store_reference_carries_a_namespace() -> None:
    """ClusterSecretStore has no namespace of its own, so ESO needs one here."""
    es = gcp_example()["externalSecret"]
    assert es["secretStore"]["kind"] == "ClusterSecretStore"
    ref = es["secretStore"]["provider"]["gcpsm"]["auth"]["workloadIdentity"][
        "serviceAccountRef"
    ]
    assert ref.get("namespace"), "ClusterSecretStore serviceAccountRef needs a namespace"


def test_gcp_example_service_account_name_is_deterministic() -> None:
    """The SA the chart creates must match the name ESO is pointed at."""
    v = gcp_example()
    ref = v["externalSecret"]["secretStore"]["provider"]["gcpsm"]["auth"][
        "workloadIdentity"
    ]["serviceAccountRef"]
    # fullnameOverride pins the SA name; without it the SA is <release>-<chart>.
    assert v["fullnameOverride"] == ref["name"]
    assert v["serviceAccount"]["create"] is True
    assert "iam.gke.io/gcp-service-account" in v["serviceAccount"]["annotations"]


def test_gcp_example_does_not_also_create_a_helm_secret() -> None:
    creds = gcp_example()["jenkins"]["credentials"]
    assert creds["create"] is False
    assert gcp_example()["externalSecret"]["enabled"] is True


def test_template_guards_cluster_store_namespace_requirements() -> None:
    template = (CHART / "templates/externalsecret.yaml").read_text()
    assert "workloadIdentity" in template
    assert "secretAccessKeySecretRef" in template
    assert template.count("ClusterSecretStore") >= 1
