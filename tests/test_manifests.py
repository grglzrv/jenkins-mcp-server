import json
import re
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(path: str):
    return list(yaml.safe_load_all((ROOT / path).read_text()))


# A complete MagicDNS hostname, anchored at both ends. Testing membership of the
# bare string "ts.net" is a host check on a substring: it matches at any offset,
# so "notts.net" and "ts.net.evil.test" both pass. CodeQL flags that pattern as
# py/incomplete-url-substring-sanitization, and it is genuinely wrong here.
TAILNET_HOST = re.compile(
    r"(?<![A-Za-z0-9.-])([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.)?ts\.net(?![A-Za-z0-9.-])"
)


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


def test_raw_mcp_services_keep_sessions_on_one_replica():
    expected = {
        "sessionAffinity": "ClientIP",
        "sessionAffinityConfig": {"clientIP": {"timeoutSeconds": 600}},
    }
    base = load("deploy/kubernetes/base/service.yaml")[0]
    standalone = next(
        doc
        for doc in load("deploy/kubernetes/minibridge/standalone-deployment.yaml")
        if doc and doc["kind"] == "Service"
    )
    for service in [base, standalone]:
        assert {
            key: service["spec"][key]
            for key in ["sessionAffinity", "sessionAffinityConfig"]
        } == expected


def test_raw_resources_use_one_component_label_without_selector_changes():
    base_paths = [
        "deploy/kubernetes/base/deployment.yaml",
        "deploy/kubernetes/base/networkpolicy.yaml",
        "deploy/kubernetes/base/pdb.yaml",
        "deploy/kubernetes/base/service.yaml",
        "deploy/kubernetes/base/serviceaccount.yaml",
    ]
    for path in base_paths:
        resource = load(path)[0]
        assert resource["metadata"]["labels"]["app.kubernetes.io/component"] == (
            "mcp-server"
        )

    deployment = load("deploy/kubernetes/base/deployment.yaml")[0]
    assert deployment["spec"]["template"]["metadata"]["labels"][
        "app.kubernetes.io/component"
    ] == "mcp-server"
    assert "app.kubernetes.io/component" not in deployment["spec"]["selector"][
        "matchLabels"
    ]

    standalone = [
        doc
        for doc in load("deploy/kubernetes/minibridge/standalone-deployment.yaml")
        if doc and doc["kind"] != "Namespace"
    ]
    for resource in standalone:
        assert resource["metadata"]["labels"]["app.kubernetes.io/component"] == (
            "mcp-server"
        )
    bridge_deployment = next(doc for doc in standalone if doc["kind"] == "Deployment")
    assert bridge_deployment["spec"]["template"]["metadata"]["labels"][
        "app.kubernetes.io/component"
    ] == "mcp-server"
    assert "app.kubernetes.io/component" not in bridge_deployment["spec"][
        "selector"
    ]["matchLabels"]


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
    # Match the zone declaration itself, not the characters anywhere in the file:
    # the parent zone must be forwarded, and a specific tailnet must not be
    # hardcoded.
    zones = re.findall(r"^\s*([A-Za-z0-9.-]*ts\.net):53\b", snippet, re.M)
    # Exact equality, not membership: the parent zone must be forwarded and no
    # specific tailnet may be hardcoded, so "ts.net" is the only valid entry.
    assert zones == ["ts.net"], f"expected only the parent ts.net zone, found {zones}"
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


def test_chart_managed_secret_uses_stable_default_key_names():
    template = (ROOT / "charts/jenkins-mcp-server/templates/secret.yaml").read_text()
    assert 'default "JENKINS_USERNAME"' in template
    assert 'default "JENKINS_TOKEN"' in template
    assert ".Values.jenkins.credentials.existingSecret" not in template


def test_raw_deploy_credentials_use_the_fixed_environment_key_contract():
    secret = load("deploy/kubernetes/secret.example.yaml")[0]
    assert set(secret["stringData"]) == {"JENKINS_USERNAME", "JENKINS_TOKEN"}

    external = load("deploy/kubernetes/external-secret-gcp.example.yaml")[0]
    target_keys = {entry["secretKey"] for entry in external["spec"]["data"]}
    assert target_keys == {"JENKINS_USERNAME", "JENKINS_TOKEN"}

    deployment = load("deploy/kubernetes/base/deployment.yaml")[0]
    env_from = deployment["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    assert {item["secretRef"]["name"] for item in env_from if "secretRef" in item} == {
        "jenkins-mcp-secrets"
    }

    standalone = load("deploy/kubernetes/minibridge/standalone-deployment.yaml")
    standalone_secret = next(
        document
        for document in standalone
        if document["kind"] == "Secret"
        and document["metadata"]["name"] == "jenkins-mcp-secrets"
    )
    assert set(standalone_secret["stringData"]) == {
        "JENKINS_USERNAME",
        "JENKINS_TOKEN",
    }


def test_base_manifests_are_environment_neutral():
    """The base must not carry an ingress tied to one controller."""
    base = ROOT / "deploy/kubernetes/base"
    assert not (base / "ingress.yaml").exists(), "ingress belongs in an overlay"
    kustomization = (base / "kustomization.yaml").read_text()
    assert "ingress.yaml" not in kustomization.replace("# No Ingress here", "")
    # No tailnet hostnames outside the Tailscale-specific pieces.
    config = (base / "config.env").read_text()
    hosts = [m.group(0) for m in TAILNET_HOST.finditer(config)]
    assert not hosts, f"tailnet hostnames in the neutral base: {hosts}"


def test_compose_offers_plain_and_single_container_minibridge_deployments():
    compose = load("compose.yaml")[0]
    plain = compose["services"]["server"]
    bridge = compose["services"]["minibridge"]
    assert plain["image"].endswith("}")
    assert bridge["image"].endswith("}-minibridge")
    assert bridge["profiles"] == ["minibridge"]
    assert bridge["environment"]["TOOLS_DENY"] == "@destructive"
    assert bridge["environment"]["MINIBRIDGE_ENDPOINT_MCP"] == "/mcp"
    for service in [plain, bridge]:
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]


def test_license_and_security_documents_are_present_and_current():
    license_text = (ROOT / "LICENSE").read_text()
    assert "MIT License" in license_text
    assert "2026" in license_text
    security = (ROOT / "SECURITY.md").read_text()
    for topic in ["valueFrom", "Minibridge controls", "passSecretKey"]:
        assert topic in security


def test_tailnet_host_pattern_matches_whole_hostnames_only():
    """Guards the matcher these tests rely on.

    A substring test for "ts.net" is what CodeQL flags as
    py/incomplete-url-substring-sanitization, and it is wrong on its own terms:
    it accepts a host that merely contains the string at any offset.
    """
    for hostname in ["ts.net", "jenkins.tail1234.ts.net", "a.b.cat-dog.ts.net"]:
        assert TAILNET_HOST.search(hostname), hostname
    for impostor in ["notts.net", "ts.network", "ts.net.evil.test", "prots.netcfg"]:
        assert not TAILNET_HOST.search(impostor), impostor
