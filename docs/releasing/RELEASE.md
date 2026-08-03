# Release process

The repository uses one application version across:

- `VERSION`
- Python `project.version`
- `jenkins_mcp_server.__version__`
- Helm chart `version`
- Helm chart `appVersion`
- Production Kustomize image tag

## Prepare a release

```bash
git checkout main
git pull --ff-only
make version VERSION=1.14.0
make install
make lint test verify-version
make helm-lint helm-template

git add .
git commit -m "chore(release): prepare v1.14.0"
git push origin main
```

## Publish

```bash
git tag -a v1.14.0 -m "Release v1.14.0"
git push origin v1.14.0
```

The `Release` workflow:

1. verifies that the Git tag equals the canonical repository version;
2. runs tests and builds the Python distribution;
3. publishes `linux/amd64` and `linux/arm64` images to GHCR;
4. publishes the Helm chart as an OCI artifact to GHCR;
5. emits SBOM/provenance attestations for the image and release assets;
6. creates a GitHub Release containing the Python distribution and packaged chart.

## Published artifacts

```text
ghcr.io/grglzrv/jenkins-mcp-server:1.14.0
ghcr.io/grglzrv/jenkins-mcp-server:1.2
ghcr.io/grglzrv/jenkins-mcp-server:1
ghcr.io/grglzrv/jenkins-mcp-server:latest
oci://ghcr.io/grglzrv/charts/jenkins-mcp-server --version 1.14.0
```
