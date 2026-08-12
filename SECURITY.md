# Security

## Threat model

This service gives an MCP client access to Jenkins using one configured Jenkins identity. Jenkins remains the final authorization boundary, while this server adds a narrower policy layer.

## Required production controls

- Use HTTPS to Jenkins and keep `JENKINS_VERIFY_TLS=true`.
- Mount the enterprise CA through `JENKINS_CA_BUNDLE`.
- Use a dedicated Jenkins service account and API token.
- Select exactly one `jenkins.credentials` source. `mcp.extraEnv` rejects
  chart-owned server and Minibridge names in any capitalisation, including the
  unprefixed Minibridge policy/auth variables, so a later explicit environment
  entry cannot replace validated credentials, policy, or gateway authentication.
- Restrict `MCP_ALLOWED_JOBS` to controlled folders. It filters job discovery,
  queue and running-build visibility, job/build mutations, and job URLs reached
  through `jenkins_admin_request`, including view and percent-encoded forms.
- Keep `MCP_ALLOW_ADMIN_REQUEST=false` unless a reviewed use case requires it.
- Keep `MCP_ALLOW_SCRIPT_CONSOLE=false`. Enabling it additionally permits
  `/script` and `/scriptText` for direct-server clients; Minibridge continues to
  refuse those paths while `sensitive-pattern-detection` is active.
- Keep `MCP_ALLOW_JOB_DELETE=false` unless the agent genuinely needs to remove jobs;
  deletion is irreversible and is gated separately from `MCP_ALLOW_JOB_WRITE`.
- Keep the default `MCP_ALLOW_DESTRUCTIVE=false` unless irreversible operations
  have an approved need. It disables job deletion/update, build stops, queue
  cancellation, and node offlining while leaving reads and creation/triggering
  available.
- Keep node mutation disabled unless explicitly needed.
- Put the `/mcp` endpoint behind an authenticated MCP gateway, service mesh, reverse proxy, or private network policy.
- Require Hermes human approval for delete, configuration update, node state, build kill, and generic admin operations.
- Use the chart's opt-in NetworkPolicy when pod-level isolation is required;
  explicitly name client namespaces and the narrow egress path to Jenkins.
  Leave it disabled when a firewall-protected external Jenkins URL is the
  intended boundary and cannot be represented safely by stable CIDRs.
- Forward audit JSONL process logs to the central SIEM. File output is an
  optional, size-rotated copy. Set `audit.requiredForReadiness=true` only when
  the file is the record of account and losing it must fail closed.
- Alert on server-side `action="policy.denied"` records. They include the
  enforcing check, target, reason, and structured job/category/action context
  when applicable. Each refused call produces one record before its policy
  error is returned. Minibridge refusals never reach the Python server, so
  collect Minibridge's logs as a separate policy source when it is enabled.
- Keep `MCP_MAX_RESPONSE_BYTES` bounded. Raise the 10 MB default only for a
  measured legitimate Jenkins API or job-config response; narrow folder
  queries first.
- Keep `MCP_MAX_REQUEST_BYTES` bounded. Its 10 MB default is applied to the
  exact encoded body before any Jenkins or CSRF-crumb request, limiting the
  controller impact of an oversized generated configuration or parameter set.
  It does not cap the MCP request already buffered by the transport.
- Rotate the Jenkins API token and never commit `.env` or certificates.
- Never put tokens, passwords, or other credentials in a Jenkins URL query.
  The server replaces complete query payloads with `?[redacted]` in its audit,
  HTTPX request, and transport-error output, but Jenkins, Minibridge, ingress,
  reverse proxies, or other infrastructure may keep their own request logs.

## Secret handling

For Kubernetes production deployments, prefer one existing Secret or External
Secrets Operator. Use `jenkins.credentials.secretKeyRefs` only when the Jenkins
user ID and API token are intentionally stored in different Secret objects. The
existing-Secret values should name the Secret and both keys explicitly, even
when they use the default `JENKINS_USERNAME` and `JENKINS_TOKEN` names. The
chart-managed `jenkins.credentials.create` path puts secret material in Helm
values and release history and is only for disposable environments. Its Secret
checksum rolls pods when values change. Existing and ESO-managed Secret
rotation requires a Deployment restart because Kubernetes does not mutate a
running process's environment.

If a credential may have been sent in a query before 2.8.2, rotate or revoke it
and treat historical audit files, container logs, and SIEM records as exposed
secret material. Redaction is not retroactive. Prefer API-token authentication
through `JENKINS_USERNAME` and `JENKINS_TOKEN`; do not pass the same token in an
administrator request path.

Minibridge authentication, remote-policer bearer tokens, TLS private keys, and
encrypted-key passphrases are always referenced from Kubernetes Secrets. A TLS
passphrase may share the certificate Secret (`tls.passSecretKey`) or use an
independent `tls.pass.valueFrom` reference. The chart deliberately does not
support inline secret values for these fields.

## Minibridge controls

The plain image does not include Minibridge. The separately published
`<version>-minibridge` image bundles Minibridge and the Python server into one
container; the chart selects it only when `minibridge.enabled=true`.

With `minibridge.mode=http`, the exposed `/mcp` endpoint is MCP 2025-03-26
Streamable HTTP. Minibridge runs Jenkins MCP Server over a private stdio pipe
inside that same container; no backend listener, sidecar, or transport adapter
is added. Network policy, Service and ingress target only Minibridge.

- Keep `minibridge.policer.enforce=true`; false is audit-only and passes denied
  requests.
- Enable exactly one policer. The default local Rego policy avoids a network
  dependency; the HTTP policer requires a trusted URL and should use a bearer
  token and verified CA.
- Use `tools.deny: ["@destructive", "@admin"]` unless those capabilities have a
  reviewed need. Denied tools are removed from discovery and refused on call.
- Pair `basicAuth` with TLS, or put the endpoint behind an authenticated private
  gateway. Basic authentication without TLS exposes the shared secret.
- Keep the root filesystem read-only and retain the `/tmp` and
  `/home/app/.config` writable mounts shown by the chart and examples.

## Important limitation

This repository does not implement end-user identity delegation to Jenkins. Every MCP caller acts as the configured Jenkins service account. For per-user authorization, place an identity-aware gateway in front and map users to separate server instances or implement delegated credentials.

## Tailscale deployment controls

- Expose the MCP server only through `ingressClassName: tailscale`; do not add a public Ingress or Funnel annotation.
- Grant only `tag:hermes` access to the MCP Tailscale Service on TCP 443.
- Grant only the Kubernetes egress proxy identity access to `svc:jenkins` on TCP 443.
- Keep Jenkins bound to loopback or a private interface and publish it with Tailscale Serve/Services.
- Treat `svc:jenkins` hosting approval and `svc:*` Kubernetes auto-approval as privileged tailnet-policy changes.
- The Tailscale overlay NetworkPolicy assumes Tailscale Operator namespace `tailscale` and Hermes namespace `hermes`; change these selectors to your real namespaces.
- Tailscale provides network identity and encrypted transport, but every Jenkins operation still executes as the configured Jenkins service account.
