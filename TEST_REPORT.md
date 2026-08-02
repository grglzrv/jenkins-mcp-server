# Test report

Validated on 2026-08-01 in the available execution environment.

## Passed locally

- Version synchronization: `VERSION`, Python package, Helm chart/appVersion,
  raw Kubernetes image, production Kustomize image, Helm example, and Argo CD
  OCI chart revision all resolve to **1.2.0**.
- Unit and manifest tests: **29 passed**.
- Measured application coverage: **98.61%** with branch coverage enabled.
- Python compilation for `src`, `tests`, `integration`, and `scripts`.
- Python line-length and basic unused-import checks across **19 Python files**.
- Bash syntax validation for the GitHub bootstrap script, Jenkins Tailscale
  installer, and Docker integration runner.
- YAML parsing and Kubernetes/Argo CD object-shape validation: **17 documents
  across 16 raw manifest files**.
- GitHub workflow YAML, JSON, TOML, Helm values, and Helm JSON Schema parsing.
- Helm default values and the production example validate against
  `values.schema.json`.

The test suite verifies Jenkins API URL construction, crumb handling, transient
retry behavior, strict job policy enforcement, job CRUD, Pipeline and
multibranch XML generation, build trigger/stop, queue and node operations,
progressive bounded logs, health endpoints, audit output, Tailscale ingress and
egress configuration, strict MagicDNS TLS targeting, CoreDNS forwarding, pod
hardening, dynamic ProxyGroup egress ports, and configurable credential keys.

## Validation delegated to GitHub Actions

The included workflows install their own pinned major tool versions and run:

- Ruff linting;
- Python wheel/sdist build and Twine metadata checks;
- Helm 4.2.3 lint, rendering, and packaging;
- local Docker image build on pull requests;
- Docker-based Jenkins LTS TLS integration testing;
- multi-architecture Buildx publishing for `linux/amd64` and `linux/arm64`;
- image SBOM/provenance and release-asset attestations;
- CodeQL, dependency review, and `pip-audit`.

## Environment limitations

Docker/Podman, Helm, kubectl, kustomize, Ruff, and Python build/Twine were not
installed and could not be downloaded from the execution shell. Therefore, a
live Jenkins container, live Tailscale Operator, Kubernetes deployment, Helm
render, container build, and registry push were not executed locally. The
repository includes reproducible CI workflows for each of those checks; the
first push and release should be reviewed before promoting the image to a
production environment.
