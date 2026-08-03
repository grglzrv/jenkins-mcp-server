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


def test_secret_examples_cover_all_three_credential_paths() -> None:
    existing = yaml.safe_load((EXAMPLES / "existing-secret.yaml").read_text())
    assert existing["jenkins"]["credentials"]["create"] is False
    assert existing["jenkins"]["credentials"]["existingSecret"]

    managed = yaml.safe_load((EXAMPLES / "chart-managed-secret.yaml").read_text())
    assert managed["jenkins"]["credentials"]["create"] is True
    # Must be empty or the chart references that Secret instead of creating one.
    assert managed["jenkins"]["credentials"]["existingSecret"] == ""
    # A real token must never be committed in an example.
    assert not managed["jenkins"]["credentials"]["token"]


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


def test_config_env_covers_every_supported_setting() -> None:
    """The raw manifests drifted from the app once; keep them in step."""
    import re

    cfg = set(
        re.findall(r"^([A-Z_]+)=", (DEPLOY / "base/config.env").read_text(), re.M)
    )
    src = set(
        re.findall(
            r'alias="(MCP_[A-Z_]+|JENKINS_[A-Z_]+)"',
            (ROOT / "src/jenkins_mcp_server/config.py").read_text(),
        )
    )
    # Credentials and the optional CA bundle come from the Secret, not the ConfigMap.
    from_secret = {"JENKINS_USERNAME", "JENKINS_TOKEN", "JENKINS_CA_BUNDLE"}
    assert (src - cfg) <= from_secret, f"config.env is missing {src - cfg - from_secret}"
    assert not (cfg - src), f"config.env sets unknown variables: {cfg - src}"


def test_minibridge_overlay_and_standalone_exist_and_parse() -> None:
    overlay = DEPLOY / "minibridge/kustomization.yaml"
    standalone = DEPLOY / "minibridge/standalone-deployment.yaml"
    assert yaml.safe_load(overlay.read_text())["kind"] == "Kustomization"
    docs = [d for d in yaml.safe_load_all(standalone.read_text()) if d]
    kinds = {d["kind"] for d in docs}
    assert {"Secret", "ConfigMap", "Deployment", "Service"} <= kinds
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
    production = yaml.safe_load(
        (DEPLOY / "overlays/production/kustomization.yaml").read_text()
    )
    assert "../../tailscale" in production["resources"]
    assert not any(r.endswith(".yaml") for r in production["resources"])
