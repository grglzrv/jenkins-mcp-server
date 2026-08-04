# Contributing

1. Create a feature branch from `main`.
2. Install development dependencies with `make install`.
3. Run `make lint test verify-version`.
4. Run `make helm-lint helm-template validate-manifests` when changing Helm,
   Kubernetes, Argo CD, Docker, or Compose files.
5. Add or update tests for behavior changes.
6. Open a pull request and wait for required checks.

## Version changes

Use one command to keep all versioned files aligned:

```bash
NEW_VERSION=1.19.0
make version VERSION="$NEW_VERSION"
```

Do not edit only `Chart.yaml`, `pyproject.toml`, or `VERSION` independently.
The version command updates the image tags, chart/app versions, Kustomize and
Argo CD manifests, Compose defaults, install commands, and release examples.
`make verify-version` also scans the whole repository, so a newly added pinned
manifest or document must be included in `scripts/version_pins.py`.
