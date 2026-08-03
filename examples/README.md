# Deployment examples

Replace every `example-tailnet.ts.net` value before deploying.

## Credentials

The chart supports three ways to supply the Jenkins API token. The token is
always injected with `secretKeyRef` and never written into the pod spec.

| Example | Who creates the Secret | Use when |
| --- | --- | --- |
| `values/existing-secret.yaml` | You, out of band | Default choice. The chart only references it. |
| `values/chart-managed-secret.yaml` | The chart, from values | Disposable environments only — the token lands in the Helm release. |
| `values/external-secrets-gcp.yaml` | External Secrets Operator | An existing `SecretStore` is already provisioned. |
| `values/external-secrets-gcp-workload-identity.yaml` | External Secrets Operator | The chart should create the `ClusterSecretStore` too, via GCP Workload Identity. |

## Networking

| Example | What it shows |
| --- | --- |
| `values/tailscale-production.yaml` | Production values for Tailscale ingress and egress, with the Jenkins MagicDNS hostname. |

## minibridge

| Example | What it shows |
| --- | --- |
| `values/minibridge.yaml` | The proxy enabled, all guardrails on, destructive tools excluded with `tools.deny: ["@destructive"]`. |
| `values/minibridge-hardened.yaml` | Strict `tools.allow: ["@read"]` allowlist, shared-secret auth, and TLS on the listener. |

Both need the `-minibridge` image variant, which the chart selects automatically.

## Raw manifests

| Path | What it is |
| --- | --- |
| `../deploy/kubernetes/base` | Plain Deployment, Service, Ingress, PDB, NetworkPolicy. |
| `../deploy/kubernetes/overlays/production` | The base plus the Tailscale proxy group, egress Service and DNSConfig. |
| `../deploy/kubernetes/minibridge` | Kustomize overlay putting minibridge in front of the base. |
| `../deploy/kubernetes/minibridge/standalone-deployment.yaml` | One self-contained file: Secret, ConfigMap, Deployment, Service. |
| `../deploy/kubernetes/secret.example.yaml` | Shape of the credentials Secret. |
| `../deploy/kubernetes/external-secret-gcp.example.yaml` | Raw `ExternalSecret` for GCP Secret Manager. |

Build any of them with `kubectl kustomize`, for example:

```bash
kubectl kustomize deploy/kubernetes/minibridge
```

## Argo CD

| Example | What it is |
| --- | --- |
| `argocd/application-oci.yaml` | Deploys the versioned Helm OCI chart from GHCR. |
| `argocd/application-git.yaml` | Deploys the chart directly from Git. |
| `argocd/repository-secret-private-ghcr.yaml` | Repository credential template for a private GHCR package. |
