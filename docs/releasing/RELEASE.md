# Release process

The repository uses one application version across:

- `VERSION`
- Python `project.version`
- `jenkins_mcp_server.__version__`
- Helm chart `version`
- Helm chart `appVersion`
- Production Kustomize image tag
- Minibridge image tags and raw deployment
- Versioned Argo CD applications and Compose defaults
- README install commands and release examples

The canonical inventory is `scripts/version_pins.py`. `make version` updates
all declared pins, and `make verify-version` scans the whole repository for a
stale application image, chart, Kustomize, Argo CD, Compose, or documentation
version that was not added to that inventory.

## Prepare a release

```bash
NEW_VERSION=1.19.0
git checkout -b "release/v$NEW_VERSION"
make version VERSION="$NEW_VERSION"
make install
make lint test verify-version
make helm-lint helm-template

git add .
git commit -m "chore(release): prepare v$NEW_VERSION"
git push origin "release/v$NEW_VERSION"
```

Open a pull request to `main` and merge only after every required check passes.

## Publish

```bash
git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION"
git push origin "v$NEW_VERSION"
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
ghcr.io/grglzrv/jenkins-mcp-server:<version>
ghcr.io/grglzrv/jenkins-mcp-server:<major>.<minor>
ghcr.io/grglzrv/jenkins-mcp-server:<major>
ghcr.io/grglzrv/jenkins-mcp-server:latest
ghcr.io/grglzrv/jenkins-mcp-server:<version>-minibridge
oci://ghcr.io/grglzrv/charts/jenkins-mcp-server --version <version>
```
