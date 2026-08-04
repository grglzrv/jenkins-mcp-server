import json
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(path: str):
    return list(yaml.safe_load_all((ROOT / path).read_text()))


def test_tailscale_ingress_is_private_and_points_to_mcp():
    ingress = load("deploy/kubernetes/tailscale/ingress.yaml")[0]
    assert ingress["spec"]["ingressClassName"] == "tailscale"
    assert "tailscale.com/funnel" not in ingress["metadata"].get("annotations", {})
    path = ingress["spec"]["rules"][0]["http"]["paths"][0]
    assert path["path"] == "/mcp"
    assert path["backend"]["service"]["name"] == "jenkins-mcp"


def test_jenkins_egress_uses_tailscale_service_and_proxy_group():
    service = load("deploy/kubernetes/tailscale/jenkins-egress-service.yaml")[0]
    assert service["spec"]["type"] == "ExternalName"
    annotations = service["metadata"]["annotations"]
    assert annotations["tailscale.com/tailnet-fqdn"].endswith(".ts.net")
    assert annotations["tailscale.com/proxy-group"] == "jenkins-egress"


def test_deployment_is_hardened_and_has_health_probes():
    deployment = load("deploy/kubernetes/base/deployment.yaml")[0]
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"


def test_chart_verifies_tls_by_default():
    values = load("charts/jenkins-mcp-server/values.yaml")[0]
    assert values["jenkins"]["verifyTls"] is True


def test_tailscale_example_targets_the_magicdns_name_not_the_service():
    """The egress proxy must be addressed by the name on the certificate.

    Using the Kubernetes Service name instead fails TLS hostname verification,
    so jenkins.url and tailnetFQDN have to name the same host.
    """
    values = load("examples/values/tailscale-production.yaml")[0]
    fqdn = values["tailscale"]["egress"]["tailnetFQDN"]
    assert fqdn.endswith(".ts.net")
    assert values["jenkins"]["url"] == f"https://{fqdn}"
    assert values["jenkins"]["url"] != (
        "https://" + values["tailscale"]["egress"].get("serviceName", "")
    )


def test_coredns_forwards_parent_ts_net_zone_and_uses_current_status_field():
    snippet = (ROOT / "deploy/kubernetes/tailscale/coredns-snippet.example").read_text()
    assert "ts.net:53" in snippet
    assert "example-tailnet.ts.net:53" not in snippet
    assert ".status.nameserver.ip" in snippet
    assert "nameserverStatus" not in snippet


def test_chart_can_optionally_own_tailscale_dnsconfig():
    values = load("charts/jenkins-mcp-server/values.yaml")[0]
    assert values["tailscale"]["magicDNS"]["createDNSConfig"] is False
    template = (
        ROOT / "charts/jenkins-mcp-server/templates/tailscale-dnsconfig.yaml"
    ).read_text()
    assert "kind: DNSConfig" in template
    assert ".Values.tailscale.magicDNS.createDNSConfig" in template


def _deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return override


def test_helm_values_and_production_example_match_schema():
    values = load("charts/jenkins-mcp-server/values.yaml")[0]
    schema = json.loads(
        (ROOT / "charts/jenkins-mcp-server/values.schema.json").read_text()
    )
    jsonschema.Draft7Validator.check_schema(schema)
    jsonschema.validate(values, schema)
    production = load("examples/values/tailscale-production.yaml")[0]
    jsonschema.validate(_deep_merge(values, production), schema)


def test_helm_ingress_and_test_pod_are_narrowly_scoped():
    values = load("charts/jenkins-mcp-server/values.yaml")[0]
    assert values["ingress"]["path"] == "/mcp"
    test_pod = (
        ROOT / "charts/jenkins-mcp-server/templates/tests/test-connection.yaml"
    ).read_text()
    assert "/readyz" in test_pod
    assert "app.kubernetes.io/component: helm-test" in test_pod
    assert "automountServiceAccountToken: false" in test_pod


def test_raw_network_policy_allows_dynamic_tailscale_proxy_ports():
    policy = load("deploy/kubernetes/base/networkpolicy.yaml")[0]
    tailscale_rules = [
        rule
        for rule in policy["spec"]["egress"]
        if any(
            peer.get("namespaceSelector", {})
            .get("matchLabels", {})
            .get("kubernetes.io/metadata.name")
            == "tailscale"
            for peer in rule.get("to", [])
        )
    ]
    assert len(tailscale_rules) == 1
    assert "ports" not in tailscale_rules[0]


def test_credential_secret_uses_configurable_key_names():
    template = (ROOT / "charts/jenkins-mcp-server/templates/secret.yaml").read_text()
    assert ".Values.jenkins.credentials.usernameKey" in template
    assert ".Values.jenkins.credentials.tokenKey" in template


def test_base_manifests_are_environment_neutral():
    """The base must not carry an ingress tied to one controller."""
    base = ROOT / "deploy/kubernetes/base"
    assert not (base / "ingress.yaml").exists(), "ingress belongs in an overlay"
    kustomization = (base / "kustomization.yaml").read_text()
    assert "ingress.yaml" not in kustomization.replace("# No Ingress here", "")
    # No tailnet hostnames outside the Tailscale-specific pieces.
    config = (base / "config.env").read_text()
    assert "ts.net" not in config
