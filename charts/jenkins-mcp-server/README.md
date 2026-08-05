# Jenkins MCP Server Helm Chart

Deploys the Jenkins MCP server with secure defaults and optional Tailscale Operator integration.

## Kubernetes compatibility

`Chart.yaml` declares `kubeVersion: ">=1.27.0-0"`, and Helm refuses to install
on anything older. Beyond rendering, each row below is the chart being installed
into a real k3s cluster by
[`chart-smoke.yml`](../../.github/workflows/chart-smoke.yml): the manifests are
accepted by the API server, the pod starts, the probes pass, the chart's own
test pod reaches the Service through the NetworkPolicy, an upgrade over the
existing release succeeds, and uninstall leaves nothing behind.

| Kubernetes | k3s image tested | Result |
| --- | --- | --- |
| 1.36 | `v1.36.2+k3s1` | ✅ install, upgrade and uninstall verified |
| 1.35 | `v1.35.6+k3s1` | ✅ install, upgrade and uninstall verified |
| 1.34 | `v1.34.9+k3s1` | ✅ install, upgrade and uninstall verified |
| 1.33 | `v1.33.13+k3s1` | ✅ install, upgrade and uninstall verified |
| 1.27 – 1.32 | ⚙️ rendered only | Above the declared minimum; not installed in CI |
| < 1.27 | ❌ refused | `kubeVersion` blocks it |

Each run performs, in order: install with `--wait`, `rollout status`,
`Deployment` becoming Available, `helm test` (which reaches the Service through
the NetworkPolicy), a request to the MCP endpoint, `helm upgrade` over the
existing release, and `helm uninstall` verified to leave no chart-owned resource
behind. The image is built from the commit under test and imported into the
cluster, so the chart is exercised against that code rather than a published tag.

It runs on every change and again as a release gate, so a chart that cannot
install never gets published.

### API versions used

All generally available, none deprecated in the supported range:

| Resource | apiVersion |
| --- | --- |
| Deployment | `apps/v1` |
| Service, ServiceAccount, ConfigMap, Secret | `v1` |
| Ingress | `networking.k8s.io/v1` |
| NetworkPolicy | `networking.k8s.io/v1` |
| PodDisruptionBudget | `policy/v1` |
| HorizontalPodAutoscaler | `autoscaling/v2` |
| ExternalSecret, SecretStore | `external-secrets.io/v1` (optional, `v1beta1` selectable) |
| ProxyGroup, DNSConfig | `tailscale.com/v1alpha1` (optional) |

`policy/v1` requires 1.21+ and `autoscaling/v2` requires 1.23+, both well below
the chart's declared minimum.

## Quick start

The chart assumes nothing about the cluster: no ingress controller, no
Tailscale, no service mesh. Two things are required — the Jenkins URL and a
Secret holding the credentials.

```bash
kubectl create namespace jenkins-mcp

kubectl -n jenkins-mcp create secret generic jenkins-mcp-secrets \
  --from-literal=JENKINS_USERNAME=<jenkins-user> \
  --from-literal=JENKINS_TOKEN='<jenkins-api-token>'

helm upgrade --install jenkins-mcp \
  oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --version 1.22.0 \
  --namespace jenkins-mcp \
  --set jenkins.url=https://jenkins.example.com
```

That gives a ClusterIP Service reachable in-cluster at
`http://jenkins-mcp-jenkins-mcp-server.jenkins-mcp.svc.cluster.local:8000/mcp`.
Installing without `jenkins.url` fails with a message saying so, rather than
deploying something that cannot reach a Jenkins.

Exposing it outside the cluster, running it behind minibridge, autoscaling and
the Tailscale integration are all opt-in. See the values reference below.

## Install from GHCR

```bash
helm registry login ghcr.io -u grglzrv
helm upgrade --install jenkins-mcp \
  oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --version 1.22.0 \
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

## Tailscale integration (optional)

Off by default. Enable it only on a cluster running the Tailscale Operator.

```yaml
jenkins:
  # The Jenkins MagicDNS name, not a Kubernetes Service name. Tailscale issues
  # the certificate for this hostname, so using anything else fails TLS
  # verification.
  url: https://jenkins.your-tailnet.ts.net

tailscale:
  enabled: true
  egress:
    enabled: true
    # Must name the same host as jenkins.url.
    tailnetFQDN: jenkins.your-tailnet.ts.net

ingress:
  enabled: true
  className: tailscale
  # Machine name only. The operator appends the tailnet suffix and publishes
  # the resulting MagicDNS name in the Ingress status.
  hostname: jenkins-mcp
  annotations:
    tailscale.com/proxy-group: jenkins-mcp-ingress
```

The cluster must resolve the tailnet zone. `tailscale.magicDNS.createDNSConfig`
creates the Operator `DNSConfig`, but it is cluster-scoped: set it true only if
this release should own it. Otherwise have the platform team create it once and
point CoreDNS at the nameserver IP in `DNSConfig.status.nameserver.ip`.

Configuring any `tailscale.*` sub-feature while `tailscale.enabled` is false
fails the render rather than silently producing nothing.

## Values reference

Only the keys most people change. `values.yaml` documents every option inline,
and `values.schema.json` validates them — an unknown key fails the render rather
than being silently ignored.

### Connection

| Key | Default | Required | Notes |
| :--- | :--- | :---: | :--- |
| `jenkins.url` | `""` | ✅ Yes | Jenkins base URL, including any path prefix. Must be the exact host the certificate is issued for |
| `jenkins.verifyTls` | `true` | — | Verifies the certificate Jenkins presents. Leave enabled |
| `jenkins.caBundle.existingSecret` | `""` | ⚪ Optional | Mount a CA from a Secret. Only for a private or self-signed issuer |
| `jenkins.caBundle.key` | `ca.crt` | ⚪ Optional | Key within that Secret |
| `jenkins.caBundlePath` | `""` | ⚪ Optional | Path to a CA already present in the image or mounted by `extraVolumes`. Mutually exclusive with `caBundle.existingSecret` |
| `jenkins.timeoutSeconds` / `maxRetries` | `30` / `3` | — | |

Neither CA setting is needed for a publicly issued certificate, which covers
Let's Encrypt, any commercial CA, and Tailscale: the container's trust store
already validates those. Reach for one only when a startup error mentions
`certificate verify failed` or `self-signed certificate`, and add the CA rather
than setting `verifyTls: false` — the latter accepts any certificate on a
connection carrying a Jenkins API token. Setting `verifyTls: false` together
with a CA bundle fails the render, since the two contradict each other.

### Credentials — pick exactly one

Credentials are **required**: the server cannot start without a username and API
token. Choose one source.

| Key | Default | Use when |
| :--- | :--- | :--- |
| `jenkins.credentials.existingSecret` | `jenkins-mcp-secrets` | You create one Secret. The default path |
| `jenkins.credentials.valueFrom` | empty | Username and token are keys in separate existing Secrets |
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

Requires the `-minibridge` image, which the chart selects automatically by
appending `minibridge.image.tagSuffix` to the app version. That tag is published
on every release alongside the default image; `edge-minibridge` tracks `main`.
It is one bundled container, not a sidecar: Minibridge spawns the Python server
over stdio.

| Key | Default | Notes |
| --- | --- | --- |
| `minibridge.enabled` | `false` | Everything below does nothing while this is false, and the render fails rather than ignoring it silently |
| `minibridge.tools.deny` | `[]` | Groups `@read` `@write` `@destructive` `@admin` `@all`, or tool names. `["@destructive"]` excludes the irreversible tools |
| `minibridge.tools.allow` | `[]` | Non-empty makes it a strict allowlist |
| `minibridge.methodsDeny` | `[]` | Deny MCP capabilities by method name |
| `minibridge.guardrails` | `[]` | Content checks: covert instructions, secrets redaction, and four more |
| `minibridge.policer.enforce` | `true` | `false` logs violations without blocking |
| `minibridge.policer.rego` / `http` | Rego / disabled | Exactly one policer must be enabled; remote HTTP URL, CA and bearer token are supported |
| `minibridge.mcp.useTempDir` | `true` | Uses the writable `/tmp` mount with a read-only root filesystem |
| `minibridge.basicAuth` / `tls` | disabled | Shared-secret auth and TLS, both from Secrets. A TLS key passphrase may come from the TLS Secret or `tls.pass.valueFrom` |

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
| `ingress.enabled` | `false` | No ingress controller is assumed |
| `ingress.className` | `""` | Empty uses the cluster default. The template adapts to the class |
| `ingress.hostname` | `""` | Machine name for Tailscale, full hostname otherwise |
| `ingress.annotations` | `{}` | Controller-specific; see `values.yaml` for examples |
| `ingress.hostRule` | `null` | `null` decides from the class: Tailscale omits `rules[].host`, others need it |
| `ingress.tls` / `tlsSecretName` | `true` / `""` | Tailscale issues its own certificate |
| `networkPolicy.enabled` | `true` | |
| `tailscale.enabled` | `false` | The whole integration is opt-in. Configuring a sub-feature while it is false fails the render |
| `tailscale.egress`, `magicDNS`, `proxyGroups` | disabled | See `values.yaml` |

### Audit

| Key | Default | Notes |
| --- | --- | --- |
| `audit.fileEnabled` | `true` | Records always go to stdout as well, which is the durable path |
| `audit.storage.type` | `emptyDir` | `pvc` needs `persistentVolumeClaim.claimName` |

## Example values files

| File | Shows |
| --- | --- |
| `examples/values/existing-secret.yaml` | The default credentials path |
| `examples/values/per-field-secret-refs.yaml` | Username and token from separate existing Secrets |
| `examples/values/chart-managed-secret.yaml` | Chart-created Secret, disposable environments |
| `examples/values/external-secrets-gcp-workload-identity.yaml` | GCP Secret Manager with Workload Identity |
| `examples/values/tailscale-production.yaml` | Tailscale ingress and egress |
| `examples/values/generic-ingress-hpa.yaml` | nginx, cert-manager, autoscaling, Tailscale off |
| `examples/values/minibridge.yaml` | Proxy with guardrails, destructive tools excluded |
| `examples/values/minibridge-hardened.yaml` | Read-only allowlist, shared-secret auth, TLS |

## Client endpoint

In-cluster, with no ingress:

```yaml
mcp_servers:
  jenkins:
    transport: streamable_http
    url: http://jenkins-mcp-jenkins-mcp-server.jenkins-mcp.svc.cluster.local:8000/mcp
```

With an ingress, use the hostname the controller assigns. Tailscale populates it
asynchronously, so read it back rather than assuming:

```bash
kubectl -n jenkins-mcp get ingress jenkins-mcp-jenkins-mcp-server \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

## Chart and application versions

`version` and `appVersion` are deliberately kept equal, and `image.tag` is left
empty so the chart uses `appVersion` as its image tag. The chart therefore
deploys the application it was released with by default. Operators can still
override `image.tag` explicitly, but the supported release pair is tested and
published together.

```bash
NEW_VERSION=1.22.0
make version VERSION="$NEW_VERSION"  # rewrites every version pin
make verify-version                 # asserts they all agree
```

Merging that to `main` triggers the release: the workflow watches the `VERSION`
path, then publishes the image, the `-minibridge` variant and the chart, all at
that version.

**Why not decouple them.** Helm allows the two to move independently, and for a
chart that packages third-party software they should. Here the chart and the
application live in one repository, are tested together, and ship together: the
k3s smoke test installs the chart with the image built from the same commit, so
"chart 1.20.1 with appVersion 1.20.0" is a combination nothing has ever
exercised. Coupling means the version you deploy is the version that was tested.

The cost is a version increment for a chart-only fix. The application source is
unchanged, but both images are rebuilt and the complete pair is tested again;
users who pin an older version are unaffected.

**What catches a forgotten bump.** CI classifies changes to the server package,
runtime images, functional chart files, Compose deployment, production
manifests, Argo CD applications, and shipped values as release-impacting. It
requires `VERSION` to be valid SemVer and strictly newer than the pull request's
base version, then names every offending path when the check fails. README and
documentation edits, tests, integration fixtures, and workflow-only changes are
exempt. The behavioral cases are covered by `tests/test_release_bump.py`.
