# Repository agent guide

This file defines how automated contributors must change, validate, and release
Jenkins MCP Server. Follow it together with `CONTRIBUTING.md` and
`docs/releasing/RELEASE.md`. When they disagree, stop and ask a maintainer rather
than inventing a new release process.

## Non-negotiable workflow

1. Start from the latest `main` and work on a dedicated branch. Never develop or
   push directly on `main`.
2. Inspect `git status` before editing. Preserve unrelated work and stage only
   files that belong to the requested change.
3. Classify the change as documentation-only or release-impacting before
   selecting a version.
4. Update code, tests, deployment surfaces, examples, and documentation together
   when they describe the same behavior. Do not leave a correct implementation
   behind stale deployment instructions.
5. Run the relevant local checks, then open a pull request to `main` for human
   review. Do not merge the pull request. Update that same PR if review or CI
   finds a problem.
6. Never create a release tag manually during normal development. A human merge
   of a PR that changes `VERSION` starts the release workflow automatically.

## Decide whether a version bump is required

Do not bump a version merely because a file changed. Use the behavior that will
ship as the decision boundary.

### No version bump

Leave `VERSION`, the Helm chart version, `appVersion`, image tags, and all other
pins unchanged when the diff only changes:

- Markdown, README files, comments, spelling, links, or examples that do not
  change deployable configuration;
- tests or integration fixtures without a runtime behavior change;
- CI, release workflow, or repository maintenance machinery only;
- contribution, security-reporting, or release-process documentation.

A docs-only PR may improve `[Unreleased]` when the correction is worth mentioning
later, but it must not promote a release entry or run `make version`. Updating a
README alone is not a reason to publish a new image or chart.

### Version bump required

A bump is required when deployable behavior changes in any of these areas:

- `src/`, Python package metadata, runtime dependencies, tools, policy, or
  transport behavior;
- `Dockerfile`, `.dockerignore`, `docker/`, or `compose.yaml`;
- functional files under `charts/jenkins-mcp-server/` other than Markdown;
- production resources under `deploy/`;
- shipped Argo CD applications or Helm values under `examples/argocd/` and
  `examples/values/`.

CI enforces this policy through `scripts/check_release_bump.py`. If a new
deployable path is introduced, update that policy and its tests so the path
cannot bypass the release guard.

## Select the correct SemVer increment

Read the current `VERSION` first. Change exactly the component justified by the
release; never bump a minor or major version merely because it is convenient.

| Change | Increment | Typical Jenkins MCP examples |
| --- | --- | --- |
| Backward-compatible defect or security fix | Patch: `X.Y.Z` → `X.Y.(Z+1)` | Correct retry classification, repair a Helm template, fix a probe, restore an existing documented behavior |
| Backward-compatible capability | Minor: `X.Y.Z` → `X.(Y+1).0` | New tool, new optional chart value, new deployment mode, new supported integration |
| Incompatible release | Major: `X.Y.Z` → `(X+1).0.0` | Removed or renamed public configuration without compatibility, incompatible tool/API contract, chart value restructuring that requires migration, removal of a supported deployment path |

Use a major bump only for deliberate breaking changes with a reviewed migration
plan. A large diff, many bug fixes, a chart-only edit, or the fact that the
current patch number is high does not justify a major bump. Several coordinated
breaking changes across the application and chart may justify one, but the
incompatibility—not the number of changed files—is the reason.

Use a prerelease suffix only when a maintainer requests a prerelease. Explain
all compatibility and migration impact in `CHANGELOG.md` and the PR body.

This repository intentionally publishes the application and its chart in
lockstep. `VERSION`, Python package version, image tag, Helm `version`, and Helm
`appVersion` remain equal. A functional chart-only fix therefore receives the
appropriate repository patch or minor bump; never edit only `Chart.yaml` or
only `appVersion`.

## Prepare release notes before changing version pins

For a release-impacting PR:

1. Complete every category under `CHANGELOG.md` `[Unreleased]` with
   user/operator impact or an explicit `None`/`None known`.
2. Choose the next SemVer using the rules above.
3. Run:

   ```bash
   make version VERSION=<next-version>
   ```

4. Review every generated change. Do not accept a mechanical rewrite that
   updates the wrong example or obscures a breaking change.
5. Run `make verify-version` after all edits.

Do not call `scripts/set_version.py` first. Release notes must exist before
`VERSION` changes because publication is triggered by that file. The supported
command promotes the notes, rewrites all declared pins, and verifies them in the
correct order.

Release notes must be professional and useful without reading the diff:

- **Highlights:** two to five important user or operator outcomes;
- **New Features:** new tools, chart values, deployment modes, or capabilities;
- **Improvements:** compatible performance, usability, reliability, or
  operability work;
- **Bug Fixes:** symptom, root behavior, and who was affected;
- **Breaking Changes:** exact incompatibility and migration steps, or `None`;
- **Known Issues:** unresolved limitation and workaround, or `None known`;
- **Security:** credential, policy, dependency, or hardening impact, or `None`;
- **Upgrade Notes:** actions required before/after rollout, or an explicit
  statement that no special action is needed.

The GitHub Release uses this changelog entry directly. Do not use raw commit
titles as release notes and never include secrets or sensitive exploitation
details.

## Version-pin inventory

`scripts/version_pins.py` is the canonical inventory. `make version` updates it;
`scripts/check_version.py` also scans the repository for unmanaged stale pins.
When adding a new pinned manifest, values file, Compose default, Argo CD
revision, or install command, add its exact pattern and expected count to the
inventory.

At minimum, a release keeps these synchronized:

- `VERSION`;
- `pyproject.toml` and `src/jenkins_mcp_server/__init__.py`;
- `charts/jenkins-mcp-server/Chart.yaml` (`version` and `appVersion`);
- direct and Minibridge image references in raw Kubernetes/Kustomize manifests;
- versioned Argo CD applications and production values examples;
- both Compose image defaults;
- versioned install/release commands in the main README, chart README,
  `CONTRIBUTING.md`, and `docs/releasing/RELEASE.md`.

Never fix a stale pin by hand in isolation. Add it to the inventory and rerun
the supported version command so future releases update it automatically.

## Update all affected surfaces

Use this matrix as a review checklist. “Review” does not mean every file must
change; it means verify the file remains accurate and change it when behavior,
defaults, commands, or configuration contracts changed.

| Changed behavior | Required review surfaces |
| --- | --- |
| Python setting, environment variable, or tool behavior | `src/`, unit tests, `.env.example`, `deploy/kubernetes/base/config.env`, Compose, chart ConfigMap/Secret wiring, values/schema, compatibility or troubleshooting docs |
| Helm value or template | `values.yaml`, `values.schema.json`, `_validate.tpl` where cross-field validation is needed, affected templates, chart README values table, production values examples, Argo CD examples, Helm render tests, k3s smoke |
| Docker/runtime image | `Dockerfile`, Minibridge Dockerfile when applicable, entrypoint, health/lifecycle assumptions, Compose, release workflow platforms/tags, Docker CI and smoke tests |
| Minibridge transport or policy | `docker/entrypoint.sh`, `docker/policy.rego`, policy tests, chart helpers/values/schema, standalone Minibridge manifest, Streamable HTTP probe, all-tools smoke, topology and operator docs |
| Credentials, TLS, or External Secrets | chart Secret/ExternalSecret templates, checksum rollout behavior, all credential-source examples, secret manifests, chart README, security/troubleshooting docs, credential rotation smoke |
| Kubernetes networking, probes, lifecycle, scaling, or availability | Helm Deployment/Service/Ingress/PDB/HPA/NetworkPolicy templates, raw manifests and Kustomize overlays, Argo CD and values examples, chart README, Kubernetes guide, supported k3s smoke matrix |
| MCP protocol, endpoint, or transport | server tests, Docker/Compose, Helm Service/Ingress/config, Minibridge path, client examples, integration probes, main public contract documentation |
| Version or release artifacts | every pin in `scripts/version_pins.py`, `CHANGELOG.md`, release workflow, package/image/chart build and publication gates |

Also review `SECURITY.md` when the threat model, reporting guidance, credential
handling, or security guarantees change. Do not modify `LICENSE` unless a
maintainer explicitly approves a legal/license change.

## Documentation placement

Keep the main `README.md` concise. It is the landing page, not the complete
operations manual. Update it only when a release changes one of these:

- the primary installation or connection command;
- a version pin managed by the release tooling;
- the public transport/security/deployment contract users must see before
  choosing the project;
- a short link needed to discover detailed documentation.

Put the full Helm values reference and chart-specific upgrade details in
`charts/jenkins-mcp-server/README.md`. Put deployment walkthroughs and failure
analysis in `docs/`, and keep the scenario index in `examples/README.md`. Avoid
copying the same long explanation into several READMEs; link to one authoritative
document instead.

Docs and manifests must describe the behavior actually tested at the same
commit. In particular, keep direct and Minibridge deployment examples explicit
about public Streamable HTTP, the private stdio child process, secret sources,
destructive-tool policy, session affinity, probes, and lifecycle behavior when
those contracts are relevant.

## Validation expectations

Run the smallest focused test while developing, then the complete applicable
gate before opening the PR.

For every code change:

```bash
make install
make lint typecheck test verify-version
make build
```

For Helm, Kubernetes, Argo CD, Docker, Compose, or examples:

```bash
make helm-lint helm-template helm-validate
make validate-manifests
```

Run focused integration checks for the behavior changed. Important remote gates
include:

- Python tests on every supported Python version;
- Helm lint, default and production renders, strict schema validation, package
  creation, and Kustomize builds;
- direct image and Minibridge image builds;
- guardrail policy tests and security scans;
- direct chart install/upgrade/uninstall and `helm test` across supported k3s
  versions;
- Minibridge enforcement across the supported k3s matrix;
- the Jenkins-backed all-tools smoke, which allows non-destructive tools and
  proves destructive tools are refused;
- Helm-managed, existing, per-field, and External Secrets credential rotation;
- Jenkins compatibility and end-to-end transport tests when relevant.

Do not claim a guarantee that was not tested. If a check cannot be run locally,
say so in the PR and rely on the named CI gate. Do not merge around a failing or
pending required check.

## Pull request and human handoff

Before publishing:

1. Rebase or refresh from current `main` without discarding unrelated work.
2. Inspect the complete diff and ensure the selected SemVer matches the actual
   compatibility impact.
3. Confirm docs-only changes did not alter `VERSION` or chart/image pins.
4. Confirm release-impacting changes did alter `VERSION` and include complete
   release notes.
5. Commit only the intended files and push the feature/release branch.
6. Open one PR to `main` describing what changed, why, compatibility/upgrade
   impact, affected deployment surfaces, and exact validation performed.
7. Leave the PR for human review. Do not merge it, do not push directly to
   `main`, and do not create a tag.

If CI or review finds a gap, fix the same branch, update documentation and tests
with the implementation, rerun relevant checks, and update the PR description.
Do not open replacement PRs merely to hide failed history.

After a human merges a versioned PR, `.github/workflows/release.yml` validates
the release again, runs the release smoke matrix, publishes amd64/arm64 direct
and Minibridge images, publishes the OCI Helm chart, attests artifacts, creates
the tag, and creates GitHub Release notes from `CHANGELOG.md`. Manual tags are
for an explicitly approved recovery only.
