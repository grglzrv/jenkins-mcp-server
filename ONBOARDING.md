# Onboarding

A guided installation of the Jenkins MCP server, written to be followed by an AI
agent working with the person who owns the Jenkins.

Work through the phases in order. Each one ends with a check, so a mistake
surfaces at the step that caused it rather than three steps later.

## Rules for the agent

Follow these for the whole install.

1. **Never invent a credential, hostname, or URL.** Stop and ask. A guessed
   Jenkins URL fails TLS verification; a guessed token silently produces 401s
   that look like a broken server.
2. **Ask before anything that changes state** — creating Kubernetes resources,
   installing a Helm release, triggering a Jenkins build. Show the exact command
   and wait.
3. **Never print a token back.** Read secrets from the person's input straight
   into `kubectl create secret`, and never echo them into logs or a summary.
4. **Do not write secrets into values files for production.** The chart-managed
   source is the installation default for disposable evaluation only; Helm
   stores those values in release history. Use an existing Secret or External
   Secrets for a durable installation.
5. **Do not disable TLS verification** to get past a certificate error. Ask
   whether Jenkins uses a private CA and mount it instead. See
   [docs/JENKINS_COMPATIBILITY.md](docs/JENKINS_COMPATIBILITY.md).
6. **Report what you actually observed.** If a check fails, say so and stop.
   Do not describe a step as done because the command was accepted.

## Phase 1 — Decide the shape of the install

Ask these before touching anything. Every one has a safe default, so a person
who does not know can say "default".

| Question | Default if unsure |
| --- | --- |
| Kubernetes with Helm, or Docker on a single host? | Kubernetes with Helm |
| What is the Jenkins base URL, exactly as the certificate is issued? | *must ask — no default* |
| Which Jenkins folders should the agent be able to touch? | `*`, then narrow |
| Should destructive tools be blocked at the proxy? | yes, `deny: ["@destructive"]` |
| Do you use the External Secrets Operator? | no, use a Kubernetes Secret |
| Do you have an ingress controller, and should this be exposed through it? | no, keep it in-cluster |
| Which AI harness will connect to this? | *ask — needed for phase 6* |
| Is Jenkins reachable only over a private network such as Tailscale? | no |

Notes on the last three:

- If they **do** use External Secrets, ask which provider (GCP Secret Manager,
  AWS Secrets Manager, Vault, ...), the `SecretStore` or `ClusterSecretStore`
  name, and the remote keys holding the Jenkins user ID and API token. Then use
  `jenkins.credentials.externalSecret.*` instead of creating a Secret by hand, and see
  `examples/values/external-secrets-gcp.yaml`.
- If they **do** want an ingress, ask for the `ingressClassName` and the
  hostname. Do not guess the class; a wrong one produces an Ingress no
  controller ever picks up.
- If Jenkins is only reachable over a private network, stop and read
  [docs/TAILSCALE.md](docs/TAILSCALE.md) before continuing; the URL and the
  egress configuration both change.

## Phase 2 — Check the prerequisites

Run these and show the output. Do not proceed past a failure.

```bash
kubectl version --output=yaml | grep -A2 serverVersion   # 1.27 or newer
helm version --short                                     # 3.8 or newer
kubectl auth can-i create deployment -n jenkins-mcp
```

The person also needs a **Jenkins API token**, not their password. It is created
at *People → their user → Security → API Token → Add new Token*. Ask them to
create one now if they have not.

The account behind that token is the real security boundary. Ask what
permissions it has, and say plainly if it is an admin account: this server can
only narrow what Jenkins already allows, never widen it.
[docs/JENKINS_COMPATIBILITY.md](docs/JENKINS_COMPATIBILITY.md) lists the least
permission each tool needs.

## Phase 3 — Create the namespace and the credentials Secret

Ask for the Jenkins user ID and its API token, then run this yourself so neither value is
echoed. Do not put them in a file.

```bash
kubectl create namespace jenkins-mcp

kubectl -n jenkins-mcp create secret generic jenkins-mcp-secrets \
  --from-literal=JENKINS_USERNAME='<actual-jenkins-login-id>' \
  --from-literal=JENKINS_TOKEN='<api-token>'
```

The first value is the actual value that replaces `{0}` in Jenkins' configured
LDAP **User search filter** for the user that generated the API token. With the
common `uid={0}` filter, use that account's LDAP `uid`; with a custom filter,
use the matching attribute value. Do not copy the placeholder or use an email
or display name unless the filter searches that field. The API token must come
from that same Jenkins user.

Check, without revealing the values:

```bash
kubectl -n jenkins-mcp get secret jenkins-mcp-secrets \
  -o jsonpath='{.data.JENKINS_USERNAME}' | base64 -d | wc -c
```

A non-zero length means the key is present. If the person uses External Secrets,
skip this phase and configure `jenkins.credentials.externalSecret.*` instead.

## Phase 4 — Install

Write the non-secret values to a file so the install is reproducible and
reviewable. This is the recommended production shape: minibridge enforcing,
destructive tools denied, no ingress, and the Secret from phase 3 referenced by
name.

```yaml
# values.yaml
jenkins:
  url: https://jenkins.example.com     # exact host on the certificate
  credentials:
    create:
      enabled: false
    existingSecret:
      enabled: true
      name: jenkins-mcp-secrets

mcp:
  allowedJobs: "*"                     # narrow to the folders they named
  allowJobDelete: false
  allowAdminRequest: false

minibridge:
  enabled: true
  tools:
    deny: ["@destructive"]             # hidden from discovery and refused
  guardrails:
    - covert-instruction-detection
    - sensitive-pattern-detection
    - secrets-redaction
  policer:
    enforce: true
```

Show the person the rendered manifests before installing:

```bash
helm template jenkins-mcp oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --namespace jenkins-mcp --values values.yaml
```

Then, with their approval:

```bash
helm upgrade --install jenkins-mcp \
  oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --namespace jenkins-mcp \
  --values values.yaml \
  --wait --timeout 5m
```

If they chose Docker instead, use `compose.yaml` at the repository root: copy
`.env.example` to `.env`, fill in `JENKINS_URL`, `JENKINS_USERNAME` and
`JENKINS_TOKEN`, then `docker compose --profile minibridge up -d`. The
`minibridge` profile runs the variant with the proxy; plain `docker compose up
-d` runs the server alone.

## Phase 5 — Verify it works

Do not report success before all four pass.

```bash
# 1. Pods ready
kubectl -n jenkins-mcp rollout status deploy/jenkins-mcp-jenkins-mcp-server

# 2. Chart's own test: reaches the service through the NetworkPolicy
helm test jenkins-mcp --namespace jenkins-mcp

# 3. The MCP endpoint answers
kubectl -n jenkins-mcp port-forward svc/jenkins-mcp-jenkins-mcp-server 8000:8000 &
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/mcp
# 400, 406 or 415 means it is serving; a session is required for a real call

# 4. It can actually reach Jenkins
kubectl -n jenkins-mcp logs deploy/jenkins-mcp-jenkins-mcp-server --tail=30
```

If step 4 shows a 401, the token is wrong. A certificate error means a private
CA: ask, and mount it with `jenkins.caBundle.existingSecret` rather than
disabling verification.

## Phase 6 — Connect their AI harness

Ask which harness they use, and configure it on that side. The server speaks
**Streamable HTTP** at the `/mcp` path; the surrounding configuration keys
differ per harness, so use its own documentation rather than assuming a shape.

For Hermes Agent, use the current `mcp_servers.<name>.url` HTTP form in
`deploy/hermes/mcp-config.yaml`; Hermes detects the transport from `url`. Do not
add `transport: streamable_http` or `timeout_seconds`. The supported timeout key
is `timeout`.

The endpoint is:

| Exposure | URL |
| --- | --- |
| In-cluster, no ingress | `http://jenkins-mcp-jenkins-mcp-server.jenkins-mcp.svc.cluster.local:8000/mcp` |
| Behind an ingress | `https://<hostname>/mcp` |
| Docker on the host | `http://localhost:8000/mcp` |

If the harness runs outside the cluster and no ingress was configured, say so
plainly: it cannot reach a ClusterIP service. The options are an ingress, a
port-forward for testing, or a private network. Do not leave them with an
endpoint that only works from inside.

Confirm the connection by asking the harness to list the available tools. With
the default values above, expect the destructive tools to be **absent** — that
is the proxy working, not a fault.

## Phase 7 — Hand over

Tell the person, in plain terms:

- Which Jenkins account this uses and what it can do.
- Which tools are available and which are denied.
- Where the credentials live, and that rotating the token means updating the
  Secret and restarting the deployment.
- That `mcp.allowedJobs` should be narrowed to the folders they actually need
  if it is still `*`.
- Where to look when something breaks:
  `kubectl -n jenkins-mcp logs deploy/jenkins-mcp-jenkins-mcp-server`.

Suggest two follow-ups worth doing once it works: narrowing `allowedJobs`, and
reducing the Jenkins account's permissions to the least the enabled tools need.
