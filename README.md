# Jenkins MCP Server

Production-ready Jenkins Model Context Protocol server for Hermes Agent and other MCP clients.

[![CI](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/ci.yml)
[![Release](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/release.yml/badge.svg)](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/release.yml)

## Capabilities

- Native MCP Streamable HTTP endpoint at `/mcp` and optional stdio transport.
- Job list/read/create/update/delete/copy/enable/disable.
- Pipeline and Git multibranch Pipeline creation and scanning.
- Build trigger, parameterized builds, running-build discovery, stop/terminate/kill.
- Queue inspection and cancellation.
- Node inspection and optional online/offline management.
- Progressive bounded console logs.
- Jenkins crumb support, retries, timeouts, nested-folder paths, and TLS verification.
- Read-only mode, job allowlist, write-category controls, and JSONL audit logging.
- Optional generic administrator REST request, disabled by default.

## Published artifacts

```text
ghcr.io/grglzrv/jenkins-mcp-server:<version>
oci://ghcr.io/grglzrv/charts/jenkins-mcp-server --version <version>
```

Release images are published for `linux/amd64` and `linux/arm64`.

## Architecture

```text
Hermes Agent
    │ HTTPS over Tailscale
    ▼
Tailscale Kubernetes Ingress
    │
    ▼
Jenkins MCP Server /mcp
    │ HTTPS through Tailscale egress
    ▼
Jenkins controller
```

Hermes never receives the Jenkins API token. The token stays in a Kubernetes Secret or external secret provider and is used only by the MCP server.

## Quick start with Docker

```bash
cp .env.example .env
# Configure Jenkins URL, username, token, and CA bundle.

docker build --build-arg APP_VERSION=$(cat VERSION) \
  -t jenkins-mcp-server:$(cat VERSION) .

docker run --rm \
  --env-file .env \
  -p 8000:8000 \
  -p 8081:8081 \
  -v "$PWD/certs:/certs:ro" \
  jenkins-mcp-server:$(cat VERSION)
```

Health endpoints:

```text
GET http://localhost:8081/healthz
GET http://localhost:8081/readyz
```

MCP endpoint:

```text
http://localhost:8000/mcp
```

## Helm installation

```bash
kubectl create namespace jenkins-mcp
kubectl -n jenkins-mcp create secret generic jenkins-mcp-secrets \
  --from-literal=JENKINS_USERNAME=hermes-jenkins \
  --from-literal=JENKINS_TOKEN='<JENKINS_API_TOKEN>'

helm upgrade --install jenkins-mcp \
  oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --version 1.2.0 \
  --namespace jenkins-mcp \
  --values examples/values/tailscale-production.yaml
```

The chart includes hardened pod settings, probes, NetworkPolicy, PodDisruptionBudget, optional External Secrets, Tailscale ingress, and Tailscale egress to Jenkins.

For strict TLS, configure `jenkins.url` with Jenkins's exact Tailscale MagicDNS
FQDN and route the tailnet DNS zone through the Operator `DNSConfig`; do not
use the Kubernetes egress Service name as the HTTPS hostname.

## Hermes configuration

Through the tailnet:

```yaml
mcp_servers:
  jenkins:
    transport: streamable_http
    url: https://jenkins-mcp.<tailnet>.ts.net/mcp
```

Directly inside the same Kubernetes cluster:

```yaml
mcp_servers:
  jenkins:
    transport: streamable_http
    url: http://jenkins-mcp.jenkins-mcp.svc.cluster.local:8000/mcp
```

## Security defaults

```env
JENKINS_VERIFY_TLS=true
MCP_READ_ONLY=false
MCP_ALLOW_JOB_WRITE=true
MCP_ALLOW_BUILD_WRITE=true
MCP_ALLOW_NODE_WRITE=false
MCP_ALLOW_ADMIN_REQUEST=false
# Destructive (irreversible) actions. MCP_ALLOW_DESTRUCTIVE is the master
# switch; each action also needs its own flag and its category flag above.
MCP_ALLOW_DESTRUCTIVE=true
# Job deletion cannot be undone, so it is opt-in even with job writes enabled.
MCP_ALLOW_JOB_DELETE=false
MCP_ALLOW_JOB_UPDATE=true
# Aborts running builds / cancels queued items. Does not affect triggering.
MCP_ALLOW_BUILD_STOP=true
MCP_ALLOWED_JOBS=AI/*,Platform/*
```

Use a dedicated Jenkins service account. Keep destructive MCP tools behind Hermes human approval and keep `jenkins_admin_request` disabled unless there is a reviewed operational requirement.

## Development

```bash
make install
make lint
make coverage
make verify-version
```

With Helm installed:

```bash
make helm-lint
make helm-template
```

Full Docker-based Jenkins TLS integration test:

```bash
make integration
```

## Releases and versioning

One canonical semantic version is synchronized across the Python package, Helm chart, application image, and production Kustomize overlay.

```bash
make version VERSION=1.2.1
git add .
git commit -m "chore(release): prepare v1.2.1"
git tag -a v1.2.1 -m "Release v1.2.1"
git push origin main v1.2.1
```

The tag publishes the multi-architecture image, Helm OCI chart, provenance/SBOM metadata, and GitHub Release.

## Repository setup

For the `grglzrv` GitHub account, follow [docs/GITHUB_SETUP.md](docs/GITHUB_SETUP.md) or run:

```bash
./scripts/github-bootstrap.sh
```

## Documentation

- [GitHub setup](docs/GITHUB_SETUP.md)
- [Release process](docs/releasing/RELEASE.md)
- [Kubernetes, Tailscale, and Argo CD](docs/KUBERNETES_TAILSCALE_ARGOCD.md)
- [Helm chart](charts/jenkins-mcp-server/README.md)
- [Security model](SECURITY.md)
- [Original source attribution](ORIGINAL_SOURCE.md)

## Attribution

Originally based on the MIT-licensed `akhilthomas236/jenkins_mcp_server` project and substantially rewritten for production deployment. This is not an official Jenkins project.
