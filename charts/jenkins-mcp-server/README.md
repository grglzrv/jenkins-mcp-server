# Jenkins MCP Server Helm Chart

Deploys the Jenkins MCP server with secure defaults and optional Tailscale Operator integration.

## Install from GHCR

```bash
helm registry login ghcr.io -u grglzrv
helm upgrade --install jenkins-mcp \
  oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --version 1.2.0 \
  --namespace jenkins-mcp \
  --create-namespace \
  --values values-production.yaml
```

For a public package, registry login is not needed for pulls.

## Required Jenkins credentials

Create the Kubernetes Secret before installing:

```bash
kubectl create namespace jenkins-mcp
kubectl -n jenkins-mcp create secret generic jenkins-mcp-secrets \
  --from-literal=JENKINS_USERNAME=hermes-jenkins \
  --from-literal=JENKINS_TOKEN='<JENKINS_API_TOKEN>'
```

Prefer `externalSecret.enabled=true` when External Secrets Operator is available.

## Tailscale DNS and TLS

Set `jenkins.url` to the exact Jenkins MagicDNS FQDN, not the Kubernetes
`ExternalName` Service. Tailscale certificates are issued for the MagicDNS
hostname, so this preserves strict hostname verification.

The cluster must resolve the tailnet zone through the Tailscale Operator
`DNSConfig`. Set `tailscale.magicDNS.createDNSConfig=true` only when this Helm
release should own the cluster-scoped resource. Otherwise, have the platform
team create it once and configure CoreDNS to forward the parent `ts.net`
zone to the nameserver IP reported in `DNSConfig.status.nameserver.ip`.

## Production values

```yaml
image:
  repository: ghcr.io/grglzrv/jenkins-mcp-server

ingress:
  enabled: true
  className: tailscale
  hostname: jenkins-mcp
  annotations:
    tailscale.com/proxy-group: jenkins-mcp-ingress

jenkins:
  url: https://jenkins.example-tailnet.ts.net
  verifyTls: true
  credentials:
    existingSecret: jenkins-mcp-secrets
  caBundle:
    existingSecret: jenkins-internal-ca
    key: ca.crt
  caBundlePath: /certs/ca.crt

tailscale:
  magicDNS:
    createDNSConfig: false
    name: ts-dns
  egress:
    enabled: true
    serviceName: jenkins-tailnet
    tailnetFQDN: jenkins.example-tailnet.ts.net
    proxyGroup: jenkins-egress

mcp:
  allowedJobs: "AI/*,Platform/*"
  allowJobWrite: true
  allowBuildWrite: true
  allowNodeWrite: false
  allowAdminRequest: false
  # Destructive actions. allowDestructive is the master switch.
  allowDestructive: true
  allowJobDelete: false   # irreversible, opt-in
  allowJobUpdate: true
  allowBuildStop: true
```

## Hermes endpoint

After Tailscale populates the Ingress address:

```bash
kubectl get ingress -n jenkins-mcp
```

Configure Hermes:

```yaml
mcp_servers:
  jenkins:
    transport: streamable_http
    url: https://jenkins-mcp.<tailnet>.ts.net/mcp
```

## Chart and application versions

The chart `version` and `appVersion` are intentionally released together. Run:

```bash
make version VERSION=1.2.1
make verify-version
```
