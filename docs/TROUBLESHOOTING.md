# Troubleshooting

Start with the pod and its two local health endpoints. `/healthz` only proves
the process is running. `/readyz` validates configuration, the configured CA
file, and optional audit-file health; it deliberately does **not** call Jenkins.

It does report what recent traffic revealed, under `jenkins`:

```json
{"jenkins": {"last_contact_age_seconds": 4.2, "last_transport_error": null}}
```

`last_contact_age_seconds` is how long ago any request last received an HTTP
response from the
controller, and is `null` on a pod that has not been asked to do anything yet.
`last_transport_error` names the class of the latest DNS, connection, timeout,
TLS, or protocol failure and clears after the next HTTP response. Neither gates
readiness: a Jenkins outage would otherwise remove every replica from the
Service, replacing an error that names the cause with a refused connection. Use
a growing age alongside a set transport error to spot a pod that has lost
Jenkins while its peers have not. The server also writes a rate-limited warning
and a recovery message to its process logs, without including the Jenkins URL or
exception text.

These `/readyz` fields belong to the direct server. With Minibridge enabled, the
public health endpoint is Minibridge's `/`; inspect the same container's logs for
the child server's Jenkins transport warnings and recovery message.

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
| `Timed out waiting for a Jenkins concurrency slot` | This replica remained at `jenkins.maxConcurrency` for longer than `jenkins.timeoutSeconds` | Inspect slow Jenkins requests and controller capacity first; raise the per-replica limit only when Jenkins has measured headroom. Remember that replicas multiply the total |
| Requests fail briefly during a rollout or scale-down | Endpoint or load-balancer removal is propagating, or an in-memory MCP session belonged to the terminated replica | Keep `preStopDelaySeconds` enabled and tune it to the slowest measured propagation path while leaving shutdown time inside `terminationGracePeriodSeconds`. Clients must reconnect and initialize after their session-owning pod exits |
| An external Jenkins URL worked until NetworkPolicy was enabled | A standard Kubernetes NetworkPolicy selects IP blocks, namespaces, or pods—not an FQDN such as `jenkins.example.com` | Keep the chart policy disabled, or model Jenkins egress with stable CIDRs, an egress gateway, or a network plugin that supports FQDN policy |
| Every Jenkins call returns 401 | The username/token pair is invalid, revoked, or mismatched | Rotate the API token and update the Secret. The token must belong to `JENKINS_USERNAME` |
| A call returns 403 without mentioning a crumb | Jenkins authenticated the account but denied the operation | Grant the permission required by that tool for the allowed job scope; do not change proxy or crumb settings for a normal permission failure |
| Writes return 403 mentioning a crumb | Jenkins rejected the CSRF crumb | Ensure the proxy preserves the crumb header. With Strict Crumb Issuer, disable *check client IP* when SNAT or an egress proxy changes the source address |
| A call returns an unexpected 302/303 redirect, including from a write that previously appeared successful | The Jenkins base URL is missing its context path, a reverse proxy/SSO sent API traffic to a login flow, or the redirect is missing/invalid/cross-origin and cannot prove Jenkins performed the action | Put the complete prefix in `JENKINS_URL` and configure the proxy/SSO to accept Jenkins API-token authentication. Do not treat the operation as completed |
| `get_job_config` alone returns 403 | The account lacks `Job/ExtendedRead` | Grant `Job/ExtendedRead` for the allowed job scope |
| A parameterised build is rejected | `trigger_build` was called without the `parameters` field and therefore used `/build` | Pass `parameters`; `{}` selects the job's configured defaults through `/buildWithParameters` |
| A job or node tool reports an invalid-name error without contacting Jenkins | The identifier is empty, whitespace-only, contains traversal segments, or a job name has leading, trailing, or repeated `/` separators | Pass the exact Jenkins full name. Invalid separators are rejected rather than normalized to a different resource |
| `create_multibranch_pipeline` rejects the repository URL or script path before contacting Jenkins | The URL is empty, malformed, contains whitespace/control characters, embedded credentials, a query, or a fragment; or the script path is not canonical and repository-relative | Put Git credentials in Jenkins and pass their ID through `credentials_id`. Use a normal HTTPS URL, `ssh://git@host/path`, or `git@host:path`, and a path such as `Jenkinsfile` or `ci/Jenkinsfile` |
| Tool descriptions are still empty after upgrading | The MCP client cached an older `tools/list` response | Reconnect and initialize a new MCP session so the client refreshes tool metadata |
| A tool is absent from `tools/list` | Minibridge denied it through `minibridge.tools` | Check `tools.allow` and `tools.deny`; deny wins. Server-side `mcp.allow*` flags do not hide tools |
| A visible tool is refused with a policy/destructive-action error | The in-process server policy rejected the call | Check `mcp.readOnly`, `mcp.allowedJobs`, the category flag, `mcp.allowDestructive`, and the operation-specific flag |
| SIEM shows repeated `policy.denied` records | A caller is repeatedly reaching a server-side policy boundary | Group by `check`, `target`, and the structured `job`, `category`, or `policy_action` fields. Investigate bursts; each refused call intentionally produces one record. Long fields retain an identifying prefix, UTF-8 byte count, and SHA-256 digest inside the 16 KiB record limit; correlate matching truncated values by digest |
| A credential may have been sent in an administrator-request query | Releases before 2.8.2 could retain the query in audit, HTTPX, container, or forwarded SIEM logs | Rotate or revoke the credential, restrict and remediate historical records under the incident-response policy, and upgrade. Current server records keep the endpoint but replace the complete query with `?[redacted]`; external proxies and Jenkins may still have their own copies |
| `jenkins_admin_request` refuses a job URL that used to work | Administrator requests now inherit `mcp.allowedJobs`, including Jenkins view aliases and percent-encoded route forms | Add the exact job or folder to `mcp.allowedJobs`; do not widen the allowlist merely to recover unrelated administrator access |
| `jenkins_admin_request` refuses `/script` or `/scriptText` | The Groovy console has a separate in-process code-execution gate, and Minibridge can independently deny the same path | For a reviewed direct-server use case, enable both `mcp.allowAdminRequest` and `mcp.allowScriptConsole`. With Minibridge, `sensitive-pattern-detection` still refuses the path; removing that guardrail weakens its other sensitive-path protections |
| An MCP session works intermittently with multiple replicas | Requests are reaching a replica that does not own the in-memory Streamable HTTP session | Keep the Service's `ClientIP` affinity, or provide equivalent ingress affinity. Reconnect after pod restarts |
| `jenkins.last_transport_error` is set and `last_contact_age_seconds` keeps growing | This pod cannot reach Jenkins; check egress, DNS, and the CA bundle | The pod stays ready on purpose: tool calls return an error naming the cause |
| `/readyz` reports `audit_log_writable: false` | The optional audit-file copy cannot be written | Fix the mount, permissions, or full volume. Process-log audit records continue; traffic is gated only with `audit.requiredForReadiness: true` |
| `Request body ... over the ... MCP_MAX_REQUEST_BYTES limit` | The exact encoded body for a job definition, administrator call, or build parameters exceeds the 10 MB default | Reduce the definition or parameter payload. Raise `mcp.maxRequestBytes` only after measuring a legitimate request; remember this controls Jenkins egress, not memory already used to receive the MCP call |
| `Request target ... over the ... MCP_MAX_REQUEST_TARGET_BYTES limit` | The exact encoded Jenkins context path, path, and query exceed the 8192-byte interoperability boundary | Reduce nested names or query selectors. Raise `mcp.maxRequestTargetBytes` only after confirming the ingress/proxy and Jenkins request-line limits; the setting does not change those upstream limits |
| `Jenkins response exceeded MCP_MAX_RESPONSE_BYTES` | A complete API, config, or crumb response exceeded the 10 MB default safety bound | Narrow the query/folder where possible. Raise `mcp.maxResponseBytes` only for a measured legitimate response |
| `health: connection limit reached; refusing excess traffic` | More than `mcp.healthMaxConnections` direct-server health connections are active, commonly because clients hold incomplete requests open | Check Service/NetworkPolicy exposure and probe behavior first. Raise the limit only for measured legitimate probe concurrency; each admitted connection owns a handler thread for at most five seconds. Minibridge uses its own health endpoint and does not consume this setting |
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
