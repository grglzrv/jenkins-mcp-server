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


def test_credential_sources_are_mutually_exclusive() -> None:
    """All three pairings must fail the render, not resolve silently."""
    validate = (CHART / "templates/_validate.tpl").read_text()
    assert validate.count("fail") >= 3
    for pair in [
        "externalSecret.enabled and jenkins.credentials.create",
        "externalSecret.enabled is true but jenkins.credentials.existingSecret",
        "jenkins.credentials.create is true but existingSecret",
    ]:
        assert pair in validate, f"missing guard: {pair}"


def test_validation_runs_before_the_secret_templates() -> None:
    """Otherwise a `required` inside secret.yaml surfaces the wrong error."""
    for name in ["secret.yaml", "externalsecret.yaml", "deployment.yaml"]:
        text = (CHART / "templates" / name).read_text()
        assert "jenkins-mcp-server.validate" in text, name


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


def test_argocd_applications_never_create_the_credentials_secret() -> None:
    for f, app in applications():
        creds = app["spec"]["source"]["helm"]["valuesObject"]["jenkins"]["credentials"]
        assert creds.get("create") is False, f"{f.name} would create a Secret from values"
        assert creds.get("existingSecret"), f"{f.name} does not reference a Secret"


def test_argocd_applications_ignore_the_operator_rewritten_ingress_host() -> None:
    """Without this Argo CD reports permanent OutOfSync against Tailscale."""
    for f, app in applications():
        diffs = app["spec"].get("ignoreDifferences", [])
        assert any(
            d.get("kind") == "Ingress" for d in diffs
        ), f"{f.name} is missing the Ingress ignoreDifferences"


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
    top = [
        m.group(1)
        for m in re.finditer(r"^  ([a-zA-Z]+):", dep, re.M)
    ]
    assert len(top) == len(set(top)), f"duplicate keys in deployment.yaml: {top}"


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
    for key in ["tools.deny", "tools.allow", "methodsDeny", "guardrails",
                "basicAuth.enabled", "tls.enabled"]:
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


def test_health_port_exposure_is_configurable() -> None:
    """/readyz reports config state; it should not be forced onto a LoadBalancer."""
    assert values()["service"]["exposeHealthPort"] is True
    svc = (CHART / "templates/service.yaml").read_text()
    assert ".Values.service.exposeHealthPort" in svc


def test_jenkins_compatibility_is_documented() -> None:
    doc = ROOT / "docs/JENKINS_COMPATIBILITY.md"
    assert doc.is_file()
    text = doc.read_text()
    for topic in ["lts-jdk21", "cloudbees-folder", "workflow-multibranch",
                  "API token", "Job/Delete"]:
        assert topic in text, f"compatibility doc should cover {topic}"
    assert "JENKINS_COMPATIBILITY.md" in (ROOT / "README.md").read_text()


def test_documented_endpoints_match_the_client() -> None:
    """The compatibility table must not drift from the code."""

    client = (ROOT / "src/jenkins_mcp_server/client.py").read_text()
    doc = (ROOT / "docs/JENKINS_COMPATIBILITY.md").read_text()
    for endpoint in ["crumbIssuer", "createItem", "doDelete", "toggleOffline",
                     "progressiveText", "cancelItem", "config.xml"]:
        assert endpoint in client, f"{endpoint} no longer used by the client"
        assert endpoint in doc, f"{endpoint} missing from the compatibility doc"


def test_autoscaling_argocd_example_ignores_replicas() -> None:
    """Otherwise Argo CD fights the HPA and reports permanent OutOfSync."""
    app = yaml.safe_load(
        (ARGOCD / "application-hpa-generic.yaml").read_text()
    )
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
    section = readme.split("## Jenkins compatibility")[1].split("## Capabilities")[0]
    # The versions actually exercised end to end must be named.
    for version in ["2.555", "2.541.3", "2.504.3", "2.504.1"]:
        assert version in section, f"{version} missing from the compatibility matrix"
    # Untested and unsupported releases must be distinguished from verified ones.
    assert "not covered by CI" in section
    assert "Not supported" in section
    # 2.50 is not a published Docker tag and must not appear as a version.
    assert "| `2.50` |" not in readme


def test_plugin_blockers_are_documented() -> None:
    doc = (ROOT / "docs/JENKINS_COMPATIBILITY.md").read_text()
    for blocker in ["cloudbees-folder", "Strict Crumb Issuer", "Job/ExtendedRead",
                    "buildWithParameters", "script approval", "path prefix"]:
        assert blocker in doc, f"{blocker} should be covered as a known blocker"


def test_chart_readme_documents_every_value() -> None:
    """The chart README had drifted to covering 7 of 32 top-level values."""
    readme = (CHART / "README.md").read_text()
    undocumented = [k for k in sorted(values()) if k not in readme]
    assert not undocumented, f"chart README omits: {undocumented}"


def test_no_stale_version_pins_anywhere() -> None:
    """Examples added after set_version.py froze at old versions."""
    import subprocess

    result = subprocess.run(
        ["python3", str(ROOT / "scripts/check_version.py")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


# --- cluster smoke test ---------------------------------------------------


def test_smoke_workflow_and_values_exist() -> None:
    wf = ROOT / ".github/workflows/chart-smoke.yml"
    vals = ROOT / ".github/smoke-values.yaml"
    assert wf.is_file() and vals.is_file()
    text = wf.read_text()
    # The value of a cluster test is what only an API server can tell you.
    for step in ["helm install", "helm test", "helm upgrade", "helm uninstall",
                 "rollout status"]:
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


def test_smoke_values_disable_what_the_cluster_lacks() -> None:
    v = yaml.safe_load((ROOT / ".github/smoke-values.yaml").read_text())
    assert v["ingress"]["enabled"] is False
    assert v["tailscale"]["egress"]["enabled"] is False
    # The NetworkPolicy must stay on: the helm test reaching the Service through
    # it is a large part of what the smoke test proves.
    assert v["networkPolicy"]["enabled"] is True


def test_chart_readme_documents_kubernetes_support() -> None:
    readme = (CHART / "README.md").read_text()
    assert "kubeVersion" in readme
    for minor in ["1.36", "1.35", "1.34", "1.33"]:
        assert minor in readme, f"Kubernetes {minor} missing from the matrix"
    for api in ["policy/v1", "autoscaling/v2", "networking.k8s.io/v1"]:
        assert api in readme, f"{api} missing from the API version table"


def test_helm_test_pod_uses_the_effective_port_and_path() -> None:
    """It targeted mcp.healthPort and /readyz, both wrong under minibridge."""
    text = (CHART / "templates/tests/test-connection.yaml").read_text()
    assert ".Values.mcp.healthPort" not in text
    assert 'jenkins-mcp-server.healthPort' in text
    assert 'jenkins-mcp-server.readyPath' in text
    # No test pod when the health port is not published on the Service.
    assert ".Values.service.exposeHealthPort" in text


def test_numeric_env_values_are_not_rendered_in_scientific_notation() -> None:
    """Helm renders 1000000 as "1e+06" without an int cast.

    The server rejects that at startup, so every pod crash-looped on default
    values. Only a real install surfaced it; rendering looked fine.
    """
    cm = (CHART / "templates/configmap.yaml").read_text()
    for key in ["maxLogBytes", "mcp.port", "healthPort", "maxRetries"]:
        line = next(ln for ln in cm.splitlines() if key in ln)
        assert "| int |" in line or "printf" in line, f"{key} needs an int cast: {line}"


def test_settings_reject_scientific_notation_so_the_cast_matters() -> None:
    """Guards the assumption behind the fix above."""
    from jenkins_mcp_server.config import Settings

    base = dict(
        JENKINS_URL="https://j.test", JENKINS_USERNAME="u", JENKINS_TOKEN="t"
    )
    assert Settings(**base, MCP_MAX_LOG_BYTES="1000000").max_log_bytes == 1000000
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        Settings(**base, MCP_MAX_LOG_BYTES="1e+06")


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
    text = (CHART / "values.yaml").read_text()
    defaults = [
        ln for ln in text.splitlines()
        if "ts.net" in ln and not ln.strip().startswith("#")
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
    for name in ["tailscale-egress-service.yaml", "tailscale-dnsconfig.yaml",
                 "tailscale-proxygroups.yaml"]:
        text = (CHART / "templates" / name).read_text()
        assert "and .Values.tailscale.enabled" in text, name


def test_chart_readme_reflects_neutral_defaults() -> None:
    """It described a Tailscale install as though it were the default."""
    readme = (CHART / "README.md").read_text()
    # A quick start that works on a plain cluster.
    assert "## Quick start" in readme
    assert "jenkins.url=https://jenkins.example.com" in readme
    # Tailscale must be presented as opt-in, not assumed.
    assert "Tailscale integration (optional)" in readme
    assert "tailscale:\n  enabled: true" in readme
    # The in-cluster endpoint must be documented, not only an ingress hostname.
    assert "svc.cluster.local:8000/mcp" in readme


def test_chart_readme_service_name_matches_the_template() -> None:
    """The documented in-cluster URL must be the name the chart renders."""
    readme = (CHART / "README.md").read_text()
    # helm fullname for release jenkins-mcp and chart jenkins-mcp-server.
    assert "jenkins-mcp-jenkins-mcp-server" in readme
