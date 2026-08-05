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
version that was not added to that inventory. The same verification also
requires a complete changelog entry for the version in `VERSION`.

## Write professional release notes

Complete every category under `[Unreleased]` before running `make version`.
Write for users and operators: summarize behavior and impact rather than
copying commit titles. Link issues or pull requests when they add useful
context. Keep a category even when it has no changes, using `None` or `None
known` so reviewers can distinguish "reviewed and not applicable" from
"forgotten".

| Category | Content |
| --- | --- |
| Highlights | Two to five high-value outcomes and their operator impact |
| New Features | New tools, chart values, deployment modes, or capabilities |
| Improvements | Non-breaking usability, performance, operability, and documentation changes |
| Bug Fixes | Corrected defects, including the symptoms and affected users |
| Breaking Changes | Incompatible behavior, removed options, and required migration; otherwise `None` |
| Known Issues | Unresolved limitations and workarounds; otherwise `None known` |
| Security | Policy, credential, dependency, or hardening impact; otherwise `None` |
| Upgrade Notes | Exact actions needed to adopt the release, or state that no special action is required |

`scripts/changelog.py` provides the release-note lifecycle:

```bash
python scripts/changelog.py validate
python scripts/changelog.py prepare "$NEW_VERSION"
python scripts/changelog.py render "$NEW_VERSION" --output release-notes.md
```

`prepare` rejects `None yet`, `TBD`, `TODO`, and other incomplete placeholders,
promotes `[Unreleased]` to `[$NEW_VERSION] - YYYY-MM-DD`, and creates a fresh
template. `render` is the same operation used by the release workflow, so the
reviewed changelog entry is the text published on GitHub.

## Prepare a release

```bash
NEW_VERSION=1.23.0
git checkout -b "release/v$NEW_VERSION"
# Complete every [Unreleased] category in CHANGELOG.md first.
make version VERSION="$NEW_VERSION"
make install
make lint test verify-version
make helm-lint helm-template

git add .
git commit -m "chore(release): prepare v$NEW_VERSION"
git push origin "release/v$NEW_VERSION"
```

Open a pull request to `main` and merge only after every required check passes.
CI compares the complete pull request against its base. Any change to the
server package, runtime images, functional chart files, Compose deployment,
production manifests, Argo CD applications, or shipped values requires a
strictly newer `VERSION`; documentation, tests, integration fixtures, and
workflow-only changes are exempt.

## Publish

Merging the prepared version pull request to `main` starts publication
automatically because `VERSION` changed. No separate tag command is required.
The workflow creates the matching tag and GitHub Release only after every build
and smoke-test gate succeeds.

For recovery, pushing a matching annotated tag triggers the same workflow. The
workflow is idempotent: if the GitHub Release already exists, it validates the
version and skips republishing artifacts.

The `Release` workflow:

1. verifies that the requested release equals the canonical repository version
   and that all deploy manifests, examples, documentation pins, and
   release-note categories are synchronized;
2. runs tests and builds the Python distribution;
3. publishes `linux/amd64` and `linux/arm64` images to GHCR;
4. publishes the Helm chart as an OCI artifact to GHCR;
5. emits SBOM/provenance attestations for the image and release assets;
6. creates a GitHub Release from the curated changelog entry, containing the
   Python distribution and packaged chart.

Every cluster smoke test completes before steps 3 and 4 publish externally.
The workflow also serializes release runs, verifies an existing tag resolves to
the requested source commit, and refuses a new version that is not newer than
the current latest GitHub Release.

## Release review checklist

- `Chart.yaml` `version` and `appVersion` equal `VERSION` and the image tag.
- The `Shipped changes bump VERSION` check reports no unversioned deployable
  paths and confirms the proposed SemVer is greater than the base version.
- All Helm, Kubernetes, Kustomize, Argo CD, Docker, Compose, examples, and
  README pins were changed by `make version`, never by hand in isolation.
- Every `[Unreleased]` category was reviewed and contains user-facing text or
  an explicit `None` statement.
- Breaking changes name the affected configuration and include migration steps.
- Known issues describe impact and a workaround where one exists.
- Security notes do not disclose secrets or unsafe exploitation detail.
- `make lint test verify-version helm-lint helm-template validate-manifests`
  passes and all required GitHub checks are green before merge. Use a manual
  tag only when recovering a release that did not start automatically.

## Published artifacts

```text
ghcr.io/grglzrv/jenkins-mcp-server:<version>
ghcr.io/grglzrv/jenkins-mcp-server:<major>.<minor>
ghcr.io/grglzrv/jenkins-mcp-server:<major>
ghcr.io/grglzrv/jenkins-mcp-server:latest
ghcr.io/grglzrv/jenkins-mcp-server:<version>-minibridge
oci://ghcr.io/grglzrv/charts/jenkins-mcp-server --version <version>
```
