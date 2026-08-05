# Jenkins MCP Server

**Give an AI agent controlled access to Jenkins — without handing it the keys.**

A Model Context Protocol server for Jenkins, built for the case where the client
is an autonomous agent rather than a person. Works with any MCP client over
Streamable HTTP.

Most of this repository is not the 23 Jenkins tools. It is the machinery that
decides which of them an agent may actually call:

- 🔒 **Two enforcement layers.** An in-process policy that always applies, and an
  optional [minibridge](https://github.com/acuvity/minibridge) proxy that filters
  tools before they reach the server and inspects content in both directions.
- 🧨 **Irreversible actions are opt-in.** `delete_job` and `jenkins_admin_request`
  are off by default. Job paths are a glob allowlist, and traversal segments are
  rejected rather than normalised.
- 🕵️ **Prompt-injection guardrails.** A Jenkins-aware Rego policy that treats the
  script console, credential stores and `$JENKINS_HOME` as sensitive, redacts API
  tokens and crumbs from responses, and detects instructions hidden in build logs.
- ✅ **Verified, not asserted.** Every tool is exercised against four Jenkins LTS
  lines in CI, and the chart is installed into real k3s clusters across four
  Kubernetes versions — install, upgrade, `helm test`, uninstall.

The Jenkins account behind it remains the outer boundary: these controls only
narrow what that account can already do.

[![CI](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/ci.yml)
[![Release](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/release.yml/badge.svg)](https://github.com/grglzrv/jenkins-mcp-server/actions/workflows/release.yml)

> [!IMPORTANT]
> ### If you are an AI agent reading this repository
>
> The person who shared this link wants you to **install the Jenkins MCP server
> for them**. Do this:
>
> 1. **Clone** this repository to the machine you operate on.
> 2. **Open [ONBOARDING.md](ONBOARDING.md)**, a phased installer written for you.
> 3. **Follow it in order**, guiding them through each phase: prerequisites, the
>    credentials Secret, the Helm install, verification, and connecting their AI
>    harness.
> 4. **Pause and ask for every real value** — the Jenkins URL, the username, the
>    API token, and any ingress or secret-store details. **Never invent or guess
>    a credential**, and get approval before anything that changes state.
>
> [ONBOARDING.md](ONBOARDING.md) is the full guided install. Start there.

## 🚀 Two ways to install

- **Let your agent do it.** Point your agent at this repository and at
  [ONBOARDING.md](ONBOARDING.md). It installs the server step by step, pausing
  for every secret and every state change.
- **By hand.** Follow [Quick start with Docker](#-quick-start-with-docker) or
  [Helm installation](#-helm-installation) below.

## 🧰 Tools

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

See [Security and guardrails](#-security-and-guardrails) for how to restrict
these at either layer.

## 🧪 Jenkins compatibility

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

| Requirement | Needed for |
| --- | --- |
| Username and **API token** | Everything. Do not use the account password |
| `cloudbees-folder` | Any job path containing `/`. Treat as required unless every job is top-level |
| `workflow-aggregator` | Pipeline tools, and the `term`/`kill` modes of `stop_build` |
| `workflow-multibranch`, `branch-api`, `git` | Multibranch tools |

A missing plugin disables only the tools that depend on it; freestyle and node
tools keep working on a core-only controller. The Jenkins account's own
permissions are the outer boundary — see
[docs/JENKINS_COMPATIBILITY.md](docs/JENKINS_COMPATIBILITY.md) for the least
permission each tool needs.

### Known issues

| Symptom | Cause | Fix |
| --- | --- | --- |
| Every call returns 401 | The token is wrong, revoked, or belongs to a different user | Reissue the API token and update the Secret |
| `certificate verify failed` at startup | Jenkins uses a private or self-signed CA | Set `jenkins.caBundle.existingSecret`. Do not disable `verifyTls` |
| Reads succeed, every write returns 403 | Strict Crumb Issuer with *check client IP*: the crumb is bound to a source address that changes behind SNAT or an egress proxy | Disable the IP check, or exclude this server |
| `get_job_config` fails, everything else works | The account has `Job/Read` but not `Job/ExtendedRead` | Grant `Job/ExtendedRead` |
| `trigger_build` rejected on a parameterised job | Triggered with no `parameters` at all, which uses `/build` | Pass `parameters`; an empty object is enough and uses the job's defaults |
| A tool is missing from `tools/list` | It is denied by `minibridge.tools`, or disabled by an `mcp.allow*` setting | Intended behaviour. Check both before assuming a fault |

Plugin, permission, proxy and scale details, and how to verify against your own
controller, are in [docs/JENKINS_COMPATIBILITY.md](docs/JENKINS_COMPATIBILITY.md).

## ⚙️ Capabilities

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

## 📦 Published artifacts

```text
ghcr.io/grglzrv/jenkins-mcp-server:<version>
ghcr.io/grglzrv/jenkins-mcp-server:<version>-minibridge
oci://ghcr.io/grglzrv/charts/jenkins-mcp-server --version <version>
```

Release images are published for `linux/amd64` and `linux/arm64`. The plain
image contains only the Python server. The `-minibridge` tag is a separately
built variant that bundles both executables in one container; Minibridge is not
a sidecar and is not downloaded at pod startup.

## 🏗️ Architecture

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

## 🐳 Quick start with Docker

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

## ☸️ Helm installation

```bash
kubectl create namespace jenkins-mcp
kubectl -n jenkins-mcp create secret generic jenkins-mcp-secrets \
  --from-literal=JENKINS_USERNAME=hermes-jenkins \
  --from-literal=JENKINS_TOKEN='<JENKINS_API_TOKEN>'

helm upgrade --install jenkins-mcp \
  oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --version 1.23.0 \
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

## 🔌 Connecting a client

The server speaks **Streamable HTTP**. Point any MCP client at the `/mcp` path
of whichever address exposes it; the exact configuration keys differ per client,
so use its own documentation for the surrounding structure.

| Deployment | Endpoint |
| --- | --- |
| Helm chart, in-cluster | `http://<release>-jenkins-mcp-server.<namespace>.svc.cluster.local:8000/mcp` |
| Raw manifests, in-cluster | `http://jenkins-mcp.jenkins-mcp.svc.cluster.local:8000/mcp` |
| Behind an ingress | `https://<ingress-host>/mcp` |

The Helm chart derives the Service name from the release, so a release named
`jenkins-mcp` in namespace `jenkins-mcp` gives
`jenkins-mcp-jenkins-mcp-server.jenkins-mcp.svc.cluster.local`. Read it back
rather than assuming:

```bash
kubectl -n <namespace> get svc -l app.kubernetes.io/name=jenkins-mcp-server \
  -o jsonpath='{.items[0].metadata.name}'
```

With an ingress, the controller assigns the hostname asynchronously:

```bash
kubectl -n <namespace> get ingress -l app.kubernetes.io/name=jenkins-mcp-server \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}'
```

Whichever client you use, it never receives the Jenkins API token. The token
stays in a Kubernetes Secret and is used only by this server.

## 🛡️ Security and guardrails

Two independent layers. The server's own policy always applies; the minibridge
proxy is optional and sits in front of it.

### Server policy — always enforced

Applied in-process, so it holds whether or not the proxy is deployed.

| Setting | Default | Effect |
| --- | --- | --- |
| `mcp.allowedJobs` | `AI/*,Platform/*` | Glob allowlist of job paths. Traversal segments are rejected |
| `mcp.readOnly` | `false` | Refuses every write tool |
| `mcp.allowJobDelete` | **`false`** | `delete_job` is opt-in; deletion is irreversible |
| `mcp.allowAdminRequest` | **`false`** | `jenkins_admin_request` is opt-in |
| `mcp.allowNodeWrite` | `false` | `set_node_offline` |

Jenkins permissions remain the outer boundary: these settings can only narrow
what the account is already allowed to do.

### minibridge proxy — optional

Enabled with `minibridge.enabled=true`, which selects the `-minibridge` image.
It filters tools before they reach the server and inspects content in both
directions.

Tool policy accepts individual names or these groups:

| Group | Tools |
| --- | --- |
| `@read` | `list_jobs`, `get_job`, `get_job_config`, `get_build_info`, `get_build_console`, `list_running_builds`, `get_queue`, `list_nodes`, `get_node` |
| `@write` | `create_job_from_xml`, `copy_job`, `enable_job`, `disable_job`, `create_pipeline_job`, `create_multibranch_pipeline`, `scan_multibranch_pipeline`, `trigger_build` |
| `@destructive` | `update_job_config`, `delete_job`, `stop_build`, `cancel_queue_item`, `set_node_offline` |
| `@admin` | `jenkins_admin_request` |
| `@all` | every tool |

```yaml
minibridge:
  enabled: true
  tools:
    deny: ["@destructive", "@admin"]   # denied tools are hidden and refused
```

Six content guardrails are available — covert instructions, sensitive Jenkins
surfaces such as the script console and credential stores, tool shadowing,
schema misuse, cross-origin tool references, and secrets redaction. All are off
by default; enable them under `minibridge.guardrails`.

CI proves this end to end: the chart is installed into k3s with
`deny: ["@destructive"]`, and a probe asserts denied tools are absent from
`tools/list` and refused on call.

Threat model, required production controls, secret handling and the known
limitations are in [SECURITY.md](SECURITY.md).

## 🛠️ Development

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

## 🏷️ Releases and versioning

One semantic version covers the Python package, the image and the chart. The
chart pins no image tag of its own:

```yaml
image:
  repository: ghcr.io/grglzrv/jenkins-mcp-server
  tag: ""        # empty means use Chart.appVersion
```

`Chart.appVersion` *is* the image tag, so a chart version identifies exactly one
application build. Chart-only changes therefore still take a full version bump —
the trade for that guarantee.

To cut a release: complete every `[Unreleased]` category in `CHANGELOG.md`, then

```bash
NEW_VERSION=1.23.0
make version VERSION="$NEW_VERSION"   # promotes the notes, rewrites every version pin
```

Commit, open a pull request, and merge once the checks pass. Merging publishes
automatically; no manual tag is needed. The workflow refuses to publish unless
the requested version matches `VERSION`, every pin agrees, the release notes are
complete, and release-impacting changes carry a strictly newer version.

Published per release:

```text
ghcr.io/grglzrv/jenkins-mcp-server:<version>       # also <major>.<minor>, <major>, latest
ghcr.io/grglzrv/jenkins-mcp-server:<version>-minibridge
oci://ghcr.io/grglzrv/charts/jenkins-mcp-server --version <version>
```

Every push to `main` also publishes `:edge`, which the chart never references;
opt in with `image.tag: edge`.

Full procedure, script reference and review checklist:
[docs/releasing/RELEASE.md](docs/releasing/RELEASE.md).

## 📚 Documentation

- [Release process](docs/releasing/RELEASE.md)
- [Jenkins compatibility: versions, plugins, permissions](docs/JENKINS_COMPATIBILITY.md)
- [Tailscale: finding your domain and wiring it up](docs/TAILSCALE.md)
- [Kubernetes, Tailscale, and Argo CD](docs/KUBERNETES_TAILSCALE_ARGOCD.md)
- [Helm chart](charts/jenkins-mcp-server/README.md)
- [Security model](SECURITY.md)

## 📄 Licence and attribution

Released under the MIT Licence — see [LICENSE](LICENSE). That covers this
repository only. The images additionally bundle
[minibridge](https://github.com/acuvity/minibridge) (Apache 2.0) and their base
image's own packages; `docker/Dockerfile.minibridge` pins the exact minibridge
release.

This is an independent project, not affiliated with or endorsed by the Jenkins
project or the Continuous Delivery Foundation. Jenkins is a registered trademark
of the Continuous Delivery Foundation.
