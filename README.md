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
oci://ghcr.io/grglzrv/charts/jenkins-mcp-server --version <version>
```

Release images are published for `linux/amd64` and `linux/arm64`.

## Architecture

Requests pass through up to two independent enforcement layers before reaching
Jenkins. The server's own policy always applies. The minibridge proxy is
optional and adds a second layer in front of it.

```mermaid
flowchart TD
    A["Agent"] --> MB

    subgraph MB["minibridge · optional proxy"]
        direction LR
        T["<b>tools</b><br/>which tools"]
        G["<b>guardrails</b><br/>content checks"]
        P["<b>policer</b><br/>enforcement"]
    end

    MB --> S["<b>jenkins mcp server</b><br/>mcp.* policy, always enforced"]
    S --> J["Jenkins"]
```

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
  --version 1.2.0 \
  --namespace jenkins-mcp \
  --values examples/values/tailscale-production.yaml
```

The chart includes hardened pod settings, probes, NetworkPolicy, PodDisruptionBudget, optional External Secrets, Tailscale ingress, and Tailscale egress to Jenkins.

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
```

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

One canonical semantic version is synchronized across the Python package, Helm chart, application image, and production Kustomize overlay.

```bash
make version VERSION=1.2.1
git add .
git commit -m "chore(release): prepare v1.2.1"
git tag -a v1.2.1 -m "Release v1.2.1"
git push origin main v1.2.1
```

The tag publishes the multi-architecture image, Helm OCI chart, provenance/SBOM metadata, and GitHub Release.

## Repository setup

For the `grglzrv` GitHub account, follow [docs/GITHUB_SETUP.md](docs/GITHUB_SETUP.md) or run:

```bash
./scripts/github-bootstrap.sh
```

## Documentation

- [GitHub setup](docs/GITHUB_SETUP.md)
- [Release process](docs/releasing/RELEASE.md)
- [Kubernetes, Tailscale, and Argo CD](docs/KUBERNETES_TAILSCALE_ARGOCD.md)
- [Helm chart](charts/jenkins-mcp-server/README.md)
- [Security model](SECURITY.md)
- [Original source attribution](ORIGINAL_SOURCE.md)

## Attribution

Originally based on the MIT-licensed `akhilthomas236/jenkins_mcp_server` project and substantially rewritten for production deployment. This is not an official Jenkins project.
