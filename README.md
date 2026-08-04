# Jenkins MCP Server

Production-ready Jenkins Model Context Protocol server for Hermes Agent and other MCP clients.

[![CI](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/ci.yml)
[![Release](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/release.yml/badge.svg)](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/release.yml)

## Tools

23 tools, grouped as the guardrail policy groups them.

| Group | Tool | Purpose |
| --- | --- | --- |
| `@read` | `list_jobs` | List jobs, optionally within a folder |
| `@read` | `get_job` | Job details and recent builds |
| `@read` | `get_job_config` | Fetch `config.xml` |
| `@read` | `get_build_info` | Build result, duration, parameters |
| `@read` | `get_build_console` | Progressive, size-bounded console log |
| `@read` | `list_running_builds` | Builds currently executing |
| `@read` | `get_queue` | Inspect the build queue |
| `@read` | `list_nodes` | List agents |
| `@read` | `get_node` | Agent details |
| `@write` | `create_job_from_xml` | Create a job from `config.xml` |
| `@write` | `create_pipeline_job` | Create a Pipeline job |
| `@write` | `create_multibranch_pipeline` | Create a Git multibranch Pipeline |
| `@write` | `scan_multibranch_pipeline` | Trigger a branch scan |
| `@write` | `copy_job` | Copy an existing job |
| `@write` | `enable_job` | Enable a job |
| `@write` | `disable_job` | Disable a job |
| `@write` | `trigger_build` | Trigger a build, with parameters |
| `@destructive` | `update_job_config` | Overwrite an existing `config.xml` |
| `@destructive` | `delete_job` | Delete a job — irreversible, opt-in |
| `@destructive` | `stop_build` | Stop, terminate, or kill a running build |
| `@destructive` | `cancel_queue_item` | Cancel a queued item |
| `@destructive` | `set_node_offline` | Take an agent offline or online |
| `@admin` | `jenkins_admin_request` | Generic Jenkins REST call — disabled by default |

See [Security and guardrails](#security-and-guardrails) for how to restrict
these at either layer.

## Jenkins compatibility

| Jenkins | Line | Status | Coverage |
| :--- | :--- | :---: | :--- |
| **2.555.x** | Current LTS | ✅ Verified | Full tool suite, every change |
| **2.541.3** | LTS | ✅ Verified | Full tool suite |
| **2.504.3** | LTS 2.504 | ✅ Verified | Full tool suite |
| **2.504.1** | LTS 2.504 | ✅ Verified | Full tool suite, pinned plugin set |
| Other 2.x | — | ⚪ Supported | Not covered by CI |
| 1.x | — | ❌ Unsupported | Different URL scheme, no folders |

"Verified" means the full end-to-end suite ran against that core in CI: create a
Pipeline job, trigger it, stream the console, stop the build, delete the job,
with all 23 tools reachable. Reproduce any row with
[`compatibility.yml`](.github/workflows/compatibility.yml).

Run the current LTS line where possible: it is the only line receiving security
backports.

### Requirements

| | |
| --- | --- |
| Authentication | Username and **API token**. Do not use the account password |
| Folder paths such as `AI/nightly` | `cloudbees-folder` |
| Pipeline tools, and `term`/`kill` on `stop_build` | `workflow-aggregator` |
| Multibranch tools | `workflow-multibranch`, `branch-api`, `git` |

A missing plugin disables the tools that depend on it. The rest, including all
freestyle and node tools, continue to work on a core-only controller.

### Known issues

| Symptom | Cause | Fix |
| --- | --- | --- |
| Reads succeed, every write returns 403 | Strict Crumb Issuer with *check client IP*; the crumb is bound to a source address that changes behind SNAT or an egress proxy | Disable the IP check, or exclude the MCP server |
| `get_job_config` fails, everything else works | The account has `Job/Read` but not `Job/ExtendedRead` | Grant `Job/ExtendedRead` |
| `trigger_build` rejected on a parameterised job | Triggered without parameters | Pass `parameters`, an empty object is enough |

Plugin, permission, proxy and scale details, and how to verify against your own
controller, are in [docs/JENKINS_COMPATIBILITY.md](docs/JENKINS_COMPATIBILITY.md).

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
ghcr.io/grglzrv/jenkins-mcp-server:<version>-minibridge
oci://ghcr.io/grglzrv/charts/jenkins-mcp-server --version <version>
```

Release images are published for `linux/amd64` and `linux/arm64`. The plain
image contains only the Python server. The `-minibridge` tag is a separately
built variant that bundles both executables in one container; Minibridge is not
a sidecar and is not downloaded at pod startup.

## Architecture

Requests pass through up to two independent enforcement layers before reaching
Jenkins. The server's own policy always applies. The minibridge proxy is
optional and adds a second layer in front of it.

![Jenkins MCP Server architecture](docs/architecture.svg)

Inside minibridge the three settings do different jobs, which is easy to
confuse because they sit side by side in `values.yaml`:

| Setting | Question it answers | Nature |
| --- | --- | --- |
| `minibridge.tools`, `minibridge.methodsDeny` | Which tools and capabilities may be called at all? | Deterministic, by name |
| `minibridge.guardrails` | Is the content flowing through safe? | Heuristic, pattern matching |
| `minibridge.policer` | Which engine evaluates, and does a violation block or only log? | Engine configuration |

`minibridge` is the component; `guardrails` is one key inside it. With
`minibridge.enabled: false` the `guardrails` list does nothing, because there is
no proxy to evaluate it.

The deployment path in the reference Kubernetes setup:

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

Or use the maintained Compose deployment, which applies a read-only root
filesystem, dropped capabilities, writable temporary mounts, and an audit
volume:

```bash
cp .env.example .env
# Edit .env, keep it out of source control, and restrict its permissions.
docker compose up server

# Run the single-container Minibridge variant instead. Its sample policy
# allows every non-destructive tool and refuses @destructive.
docker compose --profile minibridge up minibridge
```

Do not start both services together because they publish the same MCP port.

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
  --version 1.21.0 \
  --namespace jenkins-mcp \
  --values examples/values/tailscale-production.yaml
```

The chart includes hardened pod settings, probes, NetworkPolicy,
PodDisruptionBudget, optional External Secrets, Tailscale ingress, and Tailscale
egress to Jenkins. Credentials can come from one existing Secret, separate
username and token Secret references, a chart-managed development Secret, or
External Secrets Operator. See
[`per-field-secret-refs.yaml`](examples/values/per-field-secret-refs.yaml) for
the split-secret form.

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
    # Raw manifests (deploy/kubernetes) create a Service named jenkins-mcp.
    # The Helm chart names it <release>-jenkins-mcp-server, so with release
    # jenkins-mcp the host is jenkins-mcp-jenkins-mcp-server instead.
    url: http://jenkins-mcp.jenkins-mcp.svc.cluster.local:8000/mcp
```

## Security and guardrails

Two layers, applied independently.

### Layer 1 — server policy (`mcp.*`), always enforced

Enforced inside the Python process. It applies whether or not minibridge is
deployed, and cannot be bypassed by a client.

```env
JENKINS_VERIFY_TLS=true
MCP_READ_ONLY=false
MCP_ALLOW_JOB_WRITE=true
MCP_ALLOW_BUILD_WRITE=true
MCP_ALLOW_NODE_WRITE=false
MCP_ALLOW_ADMIN_REQUEST=false
MCP_ALLOWED_JOBS=AI/*,Platform/*
```

Destructive actions are gated separately from ordinary writes, so an agent can
create and trigger jobs while never being able to delete one. Each destructive
action must clear its category flag, the master switch, and its own flag.

| Variable | Chart value | Default | Covers |
| --- | --- | --- | --- |
| `MCP_ALLOW_DESTRUCTIVE` | `mcp.allowDestructive` | `true` | Master switch for all of the below |
| `MCP_ALLOW_JOB_DELETE` | `mcp.allowJobDelete` | **`false`** | `delete_job` |
| `MCP_ALLOW_JOB_UPDATE` | `mcp.allowJobUpdate` | `true` | `update_job_config` |
| `MCP_ALLOW_BUILD_STOP` | `mcp.allowBuildStop` | `true` | `stop_build`, `cancel_queue_item` |

To disable every irreversible action at once while keeping reads, job creation
and build triggering:

```yaml
mcp:
  allowDestructive: false
```

`MCP_READ_ONLY=true` overrides everything, and `MCP_ALLOWED_JOBS` restricts every
tool to matching job paths. Job names containing `.` or `..` segments are
rejected at both the policy and URL layers.

### Layer 2 — minibridge proxy (optional)

[Minibridge](https://github.com/acuvity/minibridge) terminates MCP over HTTP,
evaluates a [Rego policy](docker/policy.rego) on every request and response, and
speaks stdio to the server it spawns. It requires the `-minibridge` image built
from [`docker/Dockerfile.minibridge`](docker/Dockerfile.minibridge).

```yaml
minibridge:
  enabled: true
```

The chart selects `ghcr.io/grglzrv/jenkins-mcp-server:<version>-minibridge`,
published alongside the default image on every release. `edge-minibridge`
tracks `main`. The chart runs one container: its entrypoint starts Minibridge,
which spawns `jenkins-mcp-server --transport stdio`. Minibridge v0.8.0 is
checksum-pinned rather than fetched from `latest`, since a proxy in the request
path should not change enforcement behaviour on an unrelated rebuild.

Credential injection is unchanged with the proxy on or off: `JENKINS_TOKEN`
always arrives via `secretKeyRef` from a Kubernetes Secret, which External
Secrets can populate from GCP Secret Manager.

#### Disabling destructive tools and capabilities

`minibridge.tools` is a policy over the server's whole tool surface. **The
default is allow-all** — with both lists empty every tool and capability is
permitted, and restriction is entirely opt-in.

Entries are a bare tool name or a group:

| Group | Tools |
| --- | --- |
| `@read` | `list_jobs`, `get_job`, `get_job_config`, `get_build_info`, `get_build_console`, `list_running_builds`, `get_queue`, `list_nodes`, `get_node` |
| `@write` | `create_job_from_xml`, `copy_job`, `enable_job`, `disable_job`, `create_pipeline_job`, `create_multibranch_pipeline`, `scan_multibranch_pipeline`, `trigger_build` |
| `@destructive` | `update_job_config`, `delete_job`, `stop_build`, `cancel_queue_item`, `set_node_offline` |
| `@admin` | `jenkins_admin_request` |
| `@all` | every tool |

Exclude only the irreversible tools, keeping everything else:

```yaml
minibridge:
  enabled: true
  tools:
    deny: ["@destructive"]
```

A strict read-only deployment — a non-empty `allow` becomes an allowlist:

```yaml
minibridge:
  tools:
    allow: ["@read"]
```

`deny` always wins over `allow`. Denied tools are refused on `tools/call` **and
filtered out of `tools/list`**, so the agent never sees a tool it cannot use.
Whole MCP capabilities are gated by method name:

```yaml
minibridge:
  methodsDeny: ["resources/read"]
```

Every one of the 23 tools belongs to exactly one group, asserted by a test so
the groups cannot drift from the server.

#### Guardrails

Heuristic content inspection, independent of the tool policy above. All are off
by default; enable only what you need.

| Guardrail | What it does |
| --- | --- |
| `covert-instruction-detection` | Detects hidden or obfuscated directives in tool descriptions and responses, such as a build log carrying `<important>Do not tell the user…</important>` |
| `sensitive-pattern-detection` | Flags references to sensitive surfaces. Jenkins-aware: the script console (`/scriptText`, `/script`), credential stores, `$JENKINS_HOME`, `secrets/master.key`, path traversal, cloud metadata endpoints |
| `shadowing-pattern-detection` | Identifies tool descriptions or responses that try to override or redirect other tools |
| `schema-misuse-prevention` | Rejects out-of-schema argument names (`debug`, `note`, `metadata`, …) used to smuggle instructions |
| `cross-origin-tool-access` | Blocks descriptions and responses that reference tools outside this server. This server's own 23 tool names are excluded so they do not trip it |
| `secrets-redaction` | Replaces credentials with `[REDACTED]` in responses. Jenkins-aware: API tokens in `curl -u user:token` and `https://user:token@host` form, `JENKINS_TOKEN`, crumbs, session cookies, plus GitHub/AWS/JWT/Slack formats |

```yaml
minibridge:
  guardrails:
    - secrets-redaction
    - sensitive-pattern-detection
    - covert-instruction-detection
```

The policy has 29 OPA tests covering both directions — that clean console output
and this server's own tool names are *not* flagged, and that injection, traversal
and token leakage are. They run in CI via the `policy` job.

#### Shared-secret authentication

A lightweight auth layer checked by the policy against the `Authorization`
header. The secret is referenced from a Kubernetes Secret and never inlined in
values:

```yaml
minibridge:
  basicAuth:
    enabled: true
    existingSecret: jenkins-mcp-server-credentials
    secretKey: BASIC_AUTH_SECRET
```

If that Secret is managed by External Secrets, add the key to
`externalSecret.extraData` — otherwise the chart fails the render with an
explanation rather than producing a pod that cannot start.

Use it only in controlled environments, rotate the secret, and always pair it
with TLS (`minibridge.tls`, which also supports mTLS via `clientCASecretKey`).

#### Enforcement

```yaml
minibridge:
  policer:
    enforce: true          # false logs the verdict and lets traffic through
    rego:
      enabled: true
      policy: /policy.rego
    http:
      enabled: false       # or delegate to a remote HTTP policer
      url: ""
      token:
        existingSecret: ""
        secretKey: MINIBRIDGE_POLICER_HTTP_BEARER_TOKEN
```

The Rego and HTTP policers are mutually exclusive. For encrypted listener keys,
`minibridge.tls.passSecretKey` reads the passphrase from the TLS Secret, while
`minibridge.tls.pass.valueFrom` can reference a different Secret and key.

### Recommended posture

Use a dedicated Jenkins service account. Keep `jenkins_admin_request` disabled
unless there is a reviewed operational requirement, keep `allowJobDelete: false`,
restrict `MCP_ALLOWED_JOBS` to controlled folders, and keep destructive tools
behind human approval in the agent. Setting `minibridge.tools.deny:
["@destructive", "@admin"]` gives defence in depth: the proxy refuses the call
and never advertises the tool, and the server would refuse it anyway.

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

One canonical semantic version is synchronized across the Python package, Helm
chart, application image, and production Kustomize overlay. The chart is never
left pointing at a stale image, because it does not pin an image tag at all:

```yaml
image:
  repository: ghcr.io/grglzrv/jenkins-mcp-server
  tag: ""        # empty means use Chart.appVersion
```

So `Chart.appVersion` *is* the image tag. Bumping the version moves the chart and
the image together by construction.

```bash
NEW_VERSION=1.21.0
make version VERSION="$NEW_VERSION"     # prepares notes and rewrites every pin
git commit -am "chore(release): prepare v$NEW_VERSION"
git push origin "release/v$NEW_VERSION"
# Open a PR and merge it after every required check is green.
```

`make version` rewrites all 22 managed application-version pins across 17
files: `VERSION`,
`pyproject.toml`, `src/jenkins_mcp_server/__init__.py`, the chart's `version`
**and** `appVersion`, both README install commands, the Kustomize base,
production and minibridge overlays, the standalone minibridge deployment, the
example values, Compose deployment, and all versioned Argo CD applications.
`scripts/check_version.py` then asserts every declared pin exists exactly where
expected and agrees. It additionally scans every small UTF-8 file in the
repository, regardless of directory or extension, for application image tags,
Helm install versions, Argo CD revisions, Kustomize tags, Compose defaults, and
release examples. A newly added manifest or README with a stale version fails
CI until it is added to the canonical pin inventory.

Before it rewrites a pin, `make version` promotes the completed `[Unreleased]`
section in `CHANGELOG.md` to a dated version entry and recreates the empty
template. Every release must explicitly cover highlights, new features,
improvements, bug fixes, breaking changes, known issues, security, and upgrade
notes. Use `None` or `None known` where appropriate; placeholders such as
`None yet`, `TBD`, and `TODO` are rejected in a release entry. The GitHub
Release publishes this validated entry verbatim instead of generating primary
notes from commit titles.

When the validated version-change pull request merges to `main`, the release
workflow starts automatically. It refuses to publish anything if these do not
agree:

```bash
test "${version}" = "$(cat VERSION)"   # requested release must match VERSION
python scripts/check_version.py        # every versioned artifact must agree
python scripts/changelog.py validate   # release notes must be complete
python scripts/check_release_bump.py "$(git merge-base HEAD origin/main)"
```

The release-bump check covers application/package inputs, both runtime images,
functional chart files, Compose, production manifests, Argo CD applications,
and shipped values. It requires a strictly newer SemVer and reports every path
that needs the release. Documentation, tests, integration fixtures, and
workflow-only changes do not force a version bump.

Only after that gate passes does it build the multi-architecture image (tagged
with the full version, major/minor, major, and `latest`), package the chart at the same version, push
both to GHCR, and create the GitHub Release from the curated changelog entry
with provenance and SBOM metadata.

A matching annotated tag can still trigger the same idempotent workflow as a
manual recovery path. If the GitHub Release already exists, the run validates
the version and source tag before safely skipping republication. Release runs
are serialized so a recovery tag cannot race automatic publication.

Two consequences worth knowing:

- **`:edge` never touches the chart.** Every push to `main` publishes
  `ghcr.io/grglzrv/jenkins-mcp-server:edge`, but the chart only ever references
  `appVersion`. Edge images are opt-in via `image.tag: edge`.
- **Chart-only changes still need a full version bump**, since chart `version`
  and `appVersion` are deliberately locked together. That trades Helm's
  convention of versioning the chart independently for the guarantee that a
  chart version identifies exactly one application build.

## Documentation

- [Release process](docs/releasing/RELEASE.md)
- [Jenkins compatibility: versions, plugins, permissions](docs/JENKINS_COMPATIBILITY.md)
- [Tailscale: finding your domain and wiring it up](docs/TAILSCALE.md)
- [Kubernetes, Tailscale, and Argo CD](docs/KUBERNETES_TAILSCALE_ARGOCD.md)
- [Helm chart](charts/jenkins-mcp-server/README.md)
- [Security model](SECURITY.md)

## Attribution

This is not an official Jenkins project. Jenkins is a registered trademark of
the Continuous Delivery Foundation.
