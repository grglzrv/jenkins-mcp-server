# Troubleshooting

Start with the pod and its two local health endpoints. `/healthz` only proves
the process is running. `/readyz` validates configuration, the configured CA
file, and optional audit-file health; it deliberately does **not** call Jenkins.

It does report what recent traffic revealed, under `jenkins`:

```json
{"jenkins": {"last_success_age_seconds": 4.2, "last_error": null}}
```

`last_success_age_seconds` is how long ago a request last reached the
controller, and is `null` on a pod that has not been asked to do anything yet.
`last_error` names the class of the last transport failure. Neither gates
readiness: a Jenkins outage would otherwise remove every replica from the
Service, replacing an error that names the cause with a refused connection. Use
a growing age alongside a set `last_error` to spot a pod that has lost Jenkins
while its peers have not.

```bash
kubectl -n jenkins-mcp get pods
kubectl -n jenkins-mcp logs deployment/jenkins-mcp-jenkins-mcp-server
kubectl -n jenkins-mcp port-forward \
  service/jenkins-mcp-jenkins-mcp-server 8081:8081
curl -fsS http://127.0.0.1:8081/readyz
```

If the Service does not publish the health port, port-forward the Deployment
instead. Do not paste Jenkins tokens or Secret output into an issue.

## Symptom guide

| Symptom | What it means | What to check |
| --- | --- | --- |
| Pod never starts; the log names `JENKINS_CA_BUNDLE` | The configured CA file is absent or unreadable | Check the Secret name/key, mount, and `jenkins.caBundle.existingSecret` or `jenkins.caBundlePath` |
| A Jenkins call reports `certificate verify failed` | Jenkins presents a certificate the container trust store cannot validate | Mount the private/self-signed issuer with `jenkins.caBundle.existingSecret`; keep `jenkins.verifyTls: true` |
| `Jenkins request failed` with DNS, connection, or timeout details | The pod cannot reach the Jenkins URL | Verify the URL and context path, cluster DNS, the Jenkins firewall allowlist, and any egress policy/gateway |
| An external Jenkins URL worked until NetworkPolicy was enabled | A standard Kubernetes NetworkPolicy selects IP blocks, namespaces, or pods—not an FQDN such as `jenkins.example.com` | Keep the chart policy disabled, or model Jenkins egress with stable CIDRs, an egress gateway, or a network plugin that supports FQDN policy |
| Every Jenkins call returns 401 | The username/token pair is invalid, revoked, or mismatched | Rotate the API token and update the Secret. The token must belong to `JENKINS_USERNAME` |
| A call returns 403 without mentioning a crumb | Jenkins authenticated the account but denied the operation | Grant the permission required by that tool for the allowed job scope; do not change proxy or crumb settings for a normal permission failure |
| Writes return 403 mentioning a crumb | Jenkins rejected the CSRF crumb | Ensure the proxy preserves the crumb header. With Strict Crumb Issuer, disable *check client IP* when SNAT or an egress proxy changes the source address |
| A call returns an unexpected 302/303 redirect | The Jenkins base URL is missing its context path, or a reverse proxy/SSO sent API traffic to a login flow | Put the complete prefix in `JENKINS_URL` and configure the proxy/SSO to accept Jenkins API-token authentication |
| `get_job_config` alone returns 403 | The account lacks `Job/ExtendedRead` | Grant `Job/ExtendedRead` for the allowed job scope |
| A parameterised build is rejected | `trigger_build` was called without the `parameters` field and therefore used `/build` | Pass `parameters`; `{}` selects the job's configured defaults through `/buildWithParameters` |
| A tool is absent from `tools/list` | Minibridge denied it through `minibridge.tools` | Check `tools.allow` and `tools.deny`; deny wins. Server-side `mcp.allow*` flags do not hide tools |
| A visible tool is refused with a policy/destructive-action error | The in-process server policy rejected the call | Check `mcp.readOnly`, `mcp.allowedJobs`, the category flag, `mcp.allowDestructive`, and the operation-specific flag |
| An MCP session works intermittently with multiple replicas | Requests are reaching a replica that does not own the in-memory Streamable HTTP session | Keep the Service's `ClientIP` affinity, or provide equivalent ingress affinity. Reconnect after pod restarts |
| `jenkins.last_error` is set and `last_success_age_seconds` keeps growing | This pod cannot reach Jenkins; check egress, DNS, and the CA bundle | The pod stays ready on purpose: tool calls return an error naming the cause |
| `/readyz` reports `audit_log_writable: false` | The optional audit-file copy cannot be written | Fix the mount, permissions, or full volume. Process-log audit records continue; traffic is gated only with `audit.requiredForReadiness: true` |
| `Jenkins response exceeded MCP_MAX_RESPONSE_BYTES` | A complete API, config, or crumb response exceeded the 10 MB default safety bound | Narrow the query/folder where possible. Raise `mcp.maxResponseBytes` only for a measured legitimate response |
| `Jenkins returned malformed JSON` | Jenkins or an intermediary returned HTML, truncated data, or invalid JSON to an API endpoint | Inspect the proxy/WAF response and Jenkins logs; confirm the URL does not lead to a login page |

## NetworkPolicy and an external Jenkins controller

The chart intentionally leaves `networkPolicy.enabled: false`. A firewall that
only permits traffic from the MCP cluster is a valid outer control, while a
default-deny Kubernetes policy without a modeled egress path can block that
same authorized traffic. Enabling the chart policy requires both client ingress
selectors and a Jenkins egress route. Do not copy the Tailscale overlay for a
normal public DNS name: its selectors are specific to Tailscale proxy pods.

Check the effective policy and endpoints:

```bash
kubectl -n jenkins-mcp get networkpolicy
kubectl -n jenkins-mcp get endpointslice \
  -l kubernetes.io/service-name=jenkins-mcp-jenkins-mcp-server
kubectl -n jenkins-mcp get events --sort-by=.lastTimestamp
```

If an existing or ESO-managed credentials Secret was rotated, restart the
workload before retesting; environment variables in running containers do not
change with the Secret:

```bash
kubectl -n jenkins-mcp rollout restart deployment/jenkins-mcp-jenkins-mcp-server
kubectl -n jenkins-mcp rollout status deployment/jenkins-mcp-jenkins-mcp-server
```

## Tool policy has two layers

The server always registers all 23 tools. Its `mcp.allow*`, destructive-action,
read-only, and job-allowlist settings enforce authorization when a tool is
called. Optional Minibridge policy runs in front of the server and can remove a
denied tool from discovery as well as refuse it on call. Seeing a tool therefore
does not imply the current policy permits the requested operation.

For Jenkins plugin, permission, CSRF, and version details, see
[JENKINS_COMPATIBILITY.md](JENKINS_COMPATIBILITY.md).
