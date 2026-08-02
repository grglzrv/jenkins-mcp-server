# GitHub setup for `grglzrv`

Target repository:

```text
https://github.com/grglzrv/jenkins-mcp-server
```

## Recommended: GitHub CLI

Install and authenticate GitHub CLI, then run from the repository root:

```bash
gh auth login --hostname github.com --git-protocol ssh --web
./scripts/github-bootstrap.sh
```

The script creates the repository when it does not exist, configures `origin`, commits the files, and pushes `main`.

## Manual setup

Create an empty repository named `jenkins-mcp-server` under `grglzrv`, without generating a README or license, then run:

```bash
git init -b main
git add .
git commit -m "feat: publish Jenkins MCP server v1.2.0"
git remote add origin git@github.com:grglzrv/jenkins-mcp-server.git
git push -u origin main
```

## Repository settings

In **Settings → Actions → General**:

- Enable GitHub Actions.
- Keep actions from GitHub and verified creators enabled.
- Ensure workflows can receive the permissions declared in each workflow.
- Under **Workflow permissions**, `Read repository contents` is sufficient as
  the publishing workflows request their own scoped `packages`, `attestations`,
  `artifact-metadata`, and `id-token` permissions.

In **Settings → Branches → Add branch protection rule** for `main`:

- Require a pull request before merging.
- Require status checks: `Python 3.11`, `Python 3.12`, `Python 3.13`, `Helm chart`, and `Docker build`.
- Require branches to be up to date.
- Block force pushes and deletion.

In **Settings → General → Pull Requests**:

- Enable squash merging.
- Automatically delete head branches.

## Secrets and tokens

No custom GitHub secret is required for the included CI, edge, or release
workflows. They publish the Docker image and Helm OCI chart with the automatic
repository-scoped `GITHUB_TOKEN` and explicit least-privilege workflow
permissions. Do not create a PAT named `GITHUB_TOKEN`; GitHub injects it for
each workflow run.

You need extra credentials only outside GitHub Actions:

### Manual local package push

Create a classic personal access token with:

- `write:packages`
- `read:packages`
- `repo` is not required solely for GHCR package operations; add it only when the separate Git operation needs access to a private repository

```bash
export CR_PAT='<TOKEN>'
echo "$CR_PAT" | docker login ghcr.io -u grglzrv --password-stdin
echo "$CR_PAT" | helm registry login ghcr.io -u grglzrv --password-stdin
```

### Kubernetes pulling a private image

```bash
kubectl -n jenkins-mcp create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=grglzrv \
  --docker-password='<PAT_WITH_READ_PACKAGES>'
```

Then set:

```yaml
imagePullSecrets:
  - name: ghcr-pull
```

### Argo CD pulling a private Helm chart

Use `examples/argocd/repository-secret-private-ghcr.yaml` with a token that has `read:packages`. Store it through External Secrets or another encrypted secret mechanism rather than committing the token.


### Attestation behavior for a personal account

The workflows create signed build provenance for images and release assets.
Because `grglzrv` is a personal account, container attestation steps explicitly
disable organization-only Linked Artifact storage records while still pushing
the attestation to GHCR. If the repository is later transferred to a GitHub
organization, set `create-storage-record: true` in the image attestation steps
to enable organization Linked Artifacts.

## Package visibility

Packages published with this repository's `GITHUB_TOKEN` are linked to the
repository and normally inherit its visibility and permissions. After the first
successful release, verify both the container image and Helm OCI package under
your GitHub **Packages** page. Set them to **Public** when anonymous image and
chart pulls are required, or keep them private and grant explicit repository
access. Confirm both packages are connected to `grglzrv/jenkins-mcp-server`.

## First release

```bash
git tag -a v1.2.0 -m "Release v1.2.0"
git push origin v1.2.0
```

This publishes:

```text
ghcr.io/grglzrv/jenkins-mcp-server:1.2.0
oci://ghcr.io/grglzrv/charts/jenkins-mcp-server --version 1.2.0
```
