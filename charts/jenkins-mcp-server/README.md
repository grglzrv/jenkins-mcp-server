# Jenkins MCP Server Helm Chart

Deploys the Jenkins MCP server with secure defaults and optional Tailscale Operator integration.

## Install from GHCR

```bash
helm registry login ghcr.io -u grglzrv
helm upgrade --install jenkins-mcp \
  oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --version 1.14.0 \
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

## Values reference

Only the keys most people change. `values.yaml` documents every option inline,
and `values.schema.json` validates them — an unknown key fails the render rather
than being silently ignored.

### Connection

| Key | Default | Notes |
| --- | --- | --- |
| `jenkins.url` | `https://jenkins.example-tailnet.ts.net` | Exact FQDN; TLS hostname validation depends on it |
| `jenkins.verifyTls` | `true` | Keep it true |
| `jenkins.caBundle.existingSecret` | `""` | Only for a private or self-signed CA. Not needed for Let's Encrypt or Tailscale |
| `jenkins.timeoutSeconds` / `maxRetries` | `30` / `3` | |

### Credentials — pick exactly one

| Key | Default | Use when |
| --- | --- | --- |
| `jenkins.credentials.existingSecret` | `jenkins-mcp-secrets` | You create the Secret. The default path |
| `jenkins.credentials.create` | `false` | Disposable environments only; the token lands in the Helm release |
| `externalSecret.enabled` | `false` | External Secrets Operator manages it |

Enabling more than one fails the render, naming which value to clear.

### Server policy, always enforced

| Key | Default |
| --- | --- |
| `mcp.readOnly` | `false` |
| `mcp.allowedJobs` | `AI/*,Platform/*` |
| `mcp.allowJobWrite` / `allowBuildWrite` | `true` |
| `mcp.allowNodeWrite` / `allowAdminRequest` | `false` |
| `mcp.allowDestructive` | `true` — master switch |
| `mcp.allowJobDelete` | **`false`** — irreversible, opt-in |
| `mcp.allowJobUpdate` / `allowBuildStop` | `true` |

### minibridge proxy, optional

Requires the `-minibridge` image, which the chart selects automatically.

| Key | Default | Notes |
| --- | --- | --- |
| `minibridge.enabled` | `false` | Everything below does nothing while this is false, and the render fails rather than ignoring it silently |
| `minibridge.tools.deny` | `[]` | Groups `@read` `@write` `@destructive` `@admin` `@all`, or tool names. `["@destructive"]` excludes the irreversible tools |
| `minibridge.tools.allow` | `[]` | Non-empty makes it a strict allowlist |
| `minibridge.methodsDeny` | `[]` | Deny MCP capabilities by method name |
| `minibridge.guardrails` | `[]` | Content checks: covert instructions, secrets redaction, and four more |
| `minibridge.policer.enforce` | `true` | `false` logs violations without blocking |
| `minibridge.basicAuth` / `tls` | disabled | Shared-secret auth and TLS, both from Secrets |

### Workload

| Key | Default | Notes |
| --- | --- | --- |
| `replicaCount` | `2` | Ignored while autoscaling is on |
| `autoscaling.enabled` | `false` | HPA; the Deployment then stops declaring replicas |
| `autoscaling.minReplicas` / `maxReplicas` | `2` / `10` | |
| `resources.requests` / `limits` | `100m`/`128Mi`, `1`/`512Mi` | The HPA scales on these |
| `podDisruptionBudget.minAvailable` | `1` | Must be below the minimum pod count, or drains block. Enforced |
| `revisionHistoryLimit` | `5` | |
| `probes.*` | enabled | Startup, readiness, liveness |
| `terminationGracePeriodSeconds` | `30` | |
| `nodeSelector`, `tolerations`, `affinity`, `topologySpreadConstraints`, `priorityClassName`, `podAnnotations`, `podLabels`, `extraVolumes`, `extraVolumeMounts` | standard | |
| `podSecurityContext`, `securityContext` | hardened | Non-root uid 10001, read-only root filesystem, all capabilities dropped, seccomp `RuntimeDefault` |
| `serviceAccount.create` / `name` / `annotations` | `true` / `""` / `{}` | Annotations carry the GCP Workload Identity binding |
| `nameOverride`, `fullnameOverride` | `""` | `fullnameOverride` pins resource names, which matters when an external system references the service account by name |
| `imagePullSecrets` | `[]` | For a private registry |

### Networking

| Key | Default | Notes |
| --- | --- | --- |
| `service.type` / `port` | `ClusterIP` / `8000` | |
| `service.exposeHealthPort` | `true` | `/readyz` reports config state; set false on an externally reachable Service |
| `ingress.className` | `tailscale` | The template adapts to the class |
| `ingress.hostname` | `jenkins-mcp` | Machine name for Tailscale, full hostname otherwise |
| `ingress.hostRule` | `null` | `null` decides from the class: Tailscale omits `rules[].host`, others need it |
| `ingress.tls` / `tlsSecretName` | `true` / `""` | Tailscale issues its own certificate |
| `networkPolicy.enabled` | `true` | |
| `tailscale.egress`, `magicDNS`, `proxyGroups` | see `values.yaml` | Disable all three off a tailnet |

### Audit

| Key | Default | Notes |
| --- | --- | --- |
| `audit.fileEnabled` | `true` | Records always go to stdout as well, which is the durable path |
| `audit.storage.type` | `emptyDir` | `pvc` needs `persistentVolumeClaim.claimName` |

## Example values files

| File | Shows |
| --- | --- |
| `examples/values/existing-secret.yaml` | The default credentials path |
| `examples/values/chart-managed-secret.yaml` | Chart-created Secret, disposable environments |
| `examples/values/external-secrets-gcp-workload-identity.yaml` | GCP Secret Manager with Workload Identity |
| `examples/values/tailscale-production.yaml` | Tailscale ingress and egress |
| `examples/values/generic-ingress-hpa.yaml` | nginx, cert-manager, autoscaling, Tailscale off |
| `examples/values/minibridge.yaml` | Proxy with guardrails, destructive tools excluded |
| `examples/values/minibridge-hardened.yaml` | Read-only allowlist, shared-secret auth, TLS |

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
make version VERSION=1.14.0
make verify-version
```
