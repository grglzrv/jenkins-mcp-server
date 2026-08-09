# Onboarding

A guided installation of the Jenkins MCP server for the operator who owns the
Jenkins and deployment environment.

Work through the phases in order. Each one ends with a check, so a mistake
surfaces at the step that caused it rather than three steps later.

## Safety rules

Follow these for the whole install.

1. **Never guess a credential, hostname, or URL.** Confirm each value. A guessed
   Jenkins URL fails TLS verification; a guessed token silently produces 401s
   that look like a broken server.
2. **Review every state-changing command before running it** — especially
   creating Kubernetes resources, installing a Helm release, or triggering a
   Jenkins build.
3. **Never print a token.** Read secrets directly into `kubectl create secret`,
   and never echo them into logs, shell history, tickets, or chat.
4. **Do not write secrets into values files for production.** The chart-managed
   source is the installation default for disposable evaluation only; Helm
   stores those values in release history. Use an existing Secret or External
   Secrets for a durable installation.
5. **Do not disable TLS verification** to get past a certificate error. Check
   whether Jenkins uses a private CA and mount it instead. See
   [docs/JENKINS_COMPATIBILITY.md](docs/JENKINS_COMPATIBILITY.md).
6. **Report what you actually observed.** If a check fails, say so and stop.
   Do not describe a step as done because the command was accepted.

## Phase 1 — Decide the shape of the install

Decide these before touching anything. Every item has a safe default except the
Jenkins URL and client choice.

| Question | Default if unsure |
| --- | --- |
| Kubernetes with Helm, or Docker on a single host? | Kubernetes with Helm |
| What is the Jenkins base URL, exactly as the certificate is issued? | *required — no default* |
| Which Jenkins folders should the MCP client be able to touch? | `*`, then narrow |
| Should destructive tools be blocked at the proxy? | yes, `deny: ["@destructive"]` |
| Do you use the External Secrets Operator? | no, use a Kubernetes Secret |
| Do you have an ingress controller, and should this be exposed through it? | no, keep it in-cluster |
| Which MCP client will connect to this? | *required for phase 6* |
| Is Jenkins reachable only over a private network such as Tailscale? | no |

Notes on the last three:

- If you **do** use External Secrets, identify the provider (GCP Secret Manager,
  AWS Secrets Manager, Vault, ...), the `SecretStore` or `ClusterSecretStore`
  name, and the remote keys holding the Jenkins user ID and API token. Then use
  `jenkins.credentials.externalSecret.*` instead of creating a Secret by hand, and see
  `examples/values/external-secrets-gcp.yaml`.
- If you **do** want an ingress, set the `ingressClassName` and the
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

Create a **Jenkins API token**, not an account password, at
*People → user → Security → API Token → Add new Token*.

The account behind that token is the real security boundary. Review its
permissions, especially whether it is an admin account: this server can
only narrow what Jenkins already allows, never widen it.
[docs/JENKINS_COMPATIBILITY.md](docs/JENKINS_COMPATIBILITY.md) lists the least
permission each tool needs.

## Phase 3 — Create the namespace and the credentials Secret

Enter the Jenkins user ID and API token in a trusted shell so neither value is
echoed or pasted into chat. Do not put them in a file.

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

A non-zero length means the key is present. If you use External Secrets,
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
      usernameKey: JENKINS_USERNAME
      tokenKey: JENKINS_TOKEN

mcp:
  allowedJobs: "*"                     # narrow to the required folders
  allowDestructive: false              # master gate for irreversible actions
  allowJobDelete: false
  allowAdminRequest: false

audit:
  fileEnabled: false                   # process-log JSONL remains enabled
  # When enabling the file, defaults retain an active 50Mi file + 3 backups.
  requiredForReadiness: false          # true only if the file must fail closed

networkPolicy:
  # Disabled for a firewall-protected external Jenkins URL. Enable only after
  # modeling both client ingress and the Jenkins egress destination.
  enabled: false
  allowSameNamespace: true
  allowInternetEgress: false

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

Review the rendered manifests before installing:

```bash
helm template jenkins-mcp oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --namespace jenkins-mcp --values values.yaml
```

Then install the reviewed release:

```bash
helm upgrade --install jenkins-mcp \
  oci://ghcr.io/grglzrv/charts/jenkins-mcp-server \
  --namespace jenkins-mcp \
  --values values.yaml \
  --wait --timeout 5m
```

For Docker instead, use `compose.yaml` at the repository root: copy
`.env.example` to `.env`, fill in `JENKINS_URL`, `JENKINS_USERNAME` and
`JENKINS_TOKEN`, then `docker compose --profile minibridge up -d`. The
`minibridge` profile runs the variant with the proxy; plain `docker compose up
-d` runs the server alone.

## Phase 5 — Verify it works

Remember that `/readyz` validates local configuration and optional audit-file
health; it does not call Jenkins. If the pod is ready but a tool fails, follow
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for external Jenkins DNS,
firewall/NetworkPolicy, TLS, proxy/SSO, policy, and session-affinity checks.

Do not report success before all four pass.

```bash
# 1. Pods ready
kubectl -n jenkins-mcp rollout status deploy/jenkins-mcp-jenkins-mcp-server

# 2. Chart's own test: reaches the Service (and an enabled policy, if configured)
helm test jenkins-mcp --namespace jenkins-mcp

# 3. The MCP endpoint answers
kubectl -n jenkins-mcp port-forward svc/jenkins-mcp-jenkins-mcp-server 8000:8000 &
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/mcp
# 400, 406 or 415 means it is serving; a session is required for a real call

# 4. It can actually reach Jenkins
kubectl -n jenkins-mcp logs deploy/jenkins-mcp-jenkins-mcp-server --tail=30
```

If step 4 shows a 401, the token is wrong. A certificate error means a private
CA: confirm it, and mount it with `jenkins.caBundle.existingSecret` rather than
disabling verification.

## Phase 6 — Connect the MCP client

Configure the chosen client on its side. The server speaks
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
port-forward for testing, or a private network. Do not record an
endpoint that only works from inside.

Confirm the connection by having the client list the available tools. With
the default values above, expect the destructive tools to be **absent** — that
is the proxy working, not a fault.

## Phase 7 — Record the deployment

Record these operational details:

- Which Jenkins account this uses and what it can do.
- Which tools are available and which are denied.
- Where the credentials live, and that rotating the token means updating the
  Secret and restarting the deployment.
- Whether `mcp.allowedJobs` still needs narrowing to the required folders
  if it is still `*`.
- Where to look when something breaks: start with
  `kubectl -n jenkins-mcp logs deploy/jenkins-mcp-jenkins-mcp-server`, then use
  [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

Suggest two follow-ups worth doing once it works: narrowing `allowedJobs`, and
reducing the Jenkins account's permissions to the least the enabled tools need.
