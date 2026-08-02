# Contributing

1. Create a feature branch from `main`.
2. Install development dependencies with `make install`.
3. Run `make lint test verify-version`.
4. Run `make helm-lint helm-template` when changing Kubernetes or Helm files.
5. Add or update tests for behavior changes.
6. Open a pull request and wait for required checks.

## Version changes

Use one command to keep all versioned files aligned:

```bash
make version VERSION=1.2.1
```

Do not edit only `Chart.yaml`, `pyproject.toml`, or `VERSION` independently.
