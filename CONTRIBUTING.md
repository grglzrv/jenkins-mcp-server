# Contributing

1. Create a feature branch from `main`.
2. Install development dependencies with `make install`.
3. Add the user and operator impact to the appropriate categories under
   `CHANGELOG.md`'s `[Unreleased]` section. Keep every category present and use
   an explicit `None` when a release has no entry for that category.
4. Run `make lint test verify-version`.
5. Run `make helm-lint helm-template validate-manifests` when changing Helm,
   Kubernetes, Argo CD, Docker, or Compose files.
6. Add or update tests for behavior changes.
7. Open a pull request and wait for required checks.

## Version changes

Use one command to keep all versioned files aligned:

```bash
NEW_VERSION=2.7.2
make version VERSION="$NEW_VERSION"
```

Do not edit only `Chart.yaml`, `pyproject.toml`, or `VERSION` independently.
Before changing any pins, the version command promotes the completed
`[Unreleased]` notes to a dated release and creates a fresh template. It then
updates the image tags, chart/app versions, Kustomize and Argo CD manifests,
Compose defaults, install commands, and release examples. `make verify-version`
validates both the repository-wide pins and the release notes, so a newly added
pinned manifest or document must be included in `scripts/version_pins.py` and
an incomplete changelog cannot be released.

`make version` prepares the notes before touching any pin, and
`scripts/set_version.py` refuses to run without them, because a version bumped
without notes fails the release with the version already changed and cannot be
retriggered by a follow-up commit.

After the version pull request passes every required check and merges to
`main`, GitHub Actions publishes the matching images, chart, tag, and GitHub
Release automatically. Do not create the release tag before the merge; manual
tagging is reserved for recovery and invokes the same idempotent workflow.

Changes to server/package inputs, runtime images, functional Helm files,
Compose, production manifests, Argo CD applications, or shipped values require
a release bump—even when the application source itself is unchanged. Run
`make version VERSION=X.Y.Z`; CI verifies that the new SemVer is strictly newer
than the base branch. Documentation, tests, integration fixtures, and workflow
changes alone do not require a release.

The complete procedure, review checklist, and list of published artifacts are
in [docs/releasing/RELEASE.md](docs/releasing/RELEASE.md).
