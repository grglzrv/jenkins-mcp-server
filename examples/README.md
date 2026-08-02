# Deployment examples

- `values/tailscale-production.yaml`: production Helm values for Tailscale ingress and egress.
- `values/external-secrets-gcp.yaml`: External Secrets Operator values for GCP Secret Manager.
- `argocd/application-oci.yaml`: Argo CD deployment from the versioned Helm OCI chart in GHCR.
- `argocd/application-git.yaml`: Argo CD deployment directly from the chart in Git.
- `argocd/repository-secret-private-ghcr.yaml`: repository credential template for a private GHCR Helm package.

Replace every `example-tailnet.ts.net` value before deploying.
