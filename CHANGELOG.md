# Changelog

All notable changes are documented here. The project follows Semantic Versioning.

## [1.7.0] - 2026-08-02

### Added

Aligned the `minibridge` values with the surface exposed by Acuvity's reference
chart, which previously had no equivalent here:

- `minibridge.mode` — `http` (minibridge aio) or `websocket` (backend).
- `minibridge.log.level`, `minibridge.tracing.url` (OTEL endpoint),
  `minibridge.sbom`.
- `minibridge.tls` — TLS on the listener, plus mTLS via `clientCASecretKey`.
  Certificates are mounted from an existing Secret.
- `minibridge.policer` — nested `enforce`, `rego.{enabled,policy}` and
  `http.{enabled,url,token,caPath}`, so a remote HTTP policer can be used
  instead of the built-in Rego policy. Replaces the flat `policerEnforce`.

Environment variable names follow minibridge's own configuration
(`MINIBRIDGE_POLICER_TYPE`, `MINIBRIDGE_TLS_SERVER_CERT`,
`OTEL_EXPORTER_OTLP_ENDPOINT`, and so on), asserted by a test.

### Notes

- The reference chart's `guardrails` list contains only the six heuristic content
  detectors; it has no tool-level allow/deny. The `minibridge.tools` and
  `minibridge.methodsDeny` policy added in 1.6.0 is an extension beyond it, and
  is retained.
- Unlike the reference chart, no minibridge value accepts inline secret material.
  The basic-auth secret, policer token, and TLS key passphrase are all referenced
  from Kubernetes Secrets, so nothing sensitive is stored in values.yaml. A test
  enforces this.
- The reference chart's `policer.http` token block dereferences `.valueFrom.name`
  and `.pass.valueFrom.key`, neither of which exists on that object, so enabling
  the HTTP policer token there fails to render. The equivalent path is covered by
  a render test here.

## [1.6.0] - 2026-08-02

### Changed

- **The minibridge guardrail is now a policy over the server's whole tool and
  capability surface, not a fixed destructive-tool block.** `minibridge.tools.deny`
  and `minibridge.tools.allow` accept bare tool names or groups, and
  `minibridge.methodsDeny` gates MCP capabilities by method name.
  - Groups: `@read` (9 tools), `@write` (8), `@destructive` (5), `@admin` (1),
    and `@all`. Every one of the 23 tools belongs to exactly one group, asserted
    by a test so the groups cannot drift from the server.
  - **Default is allow-all**: with `deny` and `allow` both empty, every tool and
    capability is permitted. Restriction is entirely opt-in.
  - Excluding just the irreversible tools is `deny: ["@destructive"]`; a strict
    read-only deployment is `allow: ["@read"]`. A non-empty `allow` becomes a
    strict allowlist, and `deny` always wins over `allow`.
  - Denied tools are refused on `tools/call` *and* filtered out of `tools/list`,
    so the agent is never shown a tool it cannot use.
- The `jenkins-destructive-tool-block` guardrail flag is replaced by the tool
  policy above. The remaining six `guardrails` entries are unchanged and stay
  independent: they are heuristic content checks, not capability configuration.

### Notes

- Credential handling is unchanged: `JENKINS_TOKEN` is injected via `secretKeyRef`
  from the External Secrets-managed Secret, with the tool policy on or off.

## [1.5.0] - 2026-08-02

### Added

- **Optional minibridge proxy with Rego guardrails**, following the pattern used by
  Acuvity's MCP server registry. When `minibridge.enabled` is set, the container
  runs `minibridge aio -- jenkins-mcp-server --transport stdio`: minibridge owns the
  listener and evaluates `docker/policy.rego` on every MCP request and response,
  while the Python server speaks stdio and binds no socket.
  - `docker/policy.rego` — guardrails for covert-instruction detection, schema
    misuse, secrets redaction, cross-origin tool access, sensitive patterns, tool
    shadowing, plus a Jenkins-specific `jenkins-destructive-tool-block`.
  - `docker/policy_test.rego` — 20 OPA tests, run by a new `policy` CI job.
  - `docker/Dockerfile.minibridge` and `docker/entrypoint.sh`.
  - Chart values: `minibridge.{enabled,image,port,healthPort,policerEnforce,
    guardrails,basicAuth}`.
- Jenkins-specific guardrail coverage: the script console (`/scriptText`,
  `/script`), credential stores, `$JENKINS_HOME`, and `secrets/master.key` are
  treated as sensitive; API tokens are redacted in the `curl -u user:token` and
  `https://user:token@host` forms that leak through build console output.
- Shared-secret authentication via `minibridge.basicAuth`, injected from a
  Kubernetes Secret rather than inlined in values.

### Fixed

- Pointing `minibridge.basicAuth.existingSecret` at the ExternalSecret target
  without adding the key to `externalSecret.extraData` produced a pod that failed
  with `CreateContainerConfigError`. This now fails the render with guidance.
- Removed a duplicate `jenkins-mcp-server.image` helper definition.

### Notes

- Credential injection is unchanged and verified with minibridge both on and off:
  `JENKINS_TOKEN` always arrives via `secretKeyRef` from the Secret that External
  Secrets creates, and is never inlined into the chart or baked into the image.
- The minibridge image is a separate build (`-minibridge` tag suffix); the default
  image is unaffected.

## [1.4.0] - 2026-08-02

### Added

- **Destructive actions can now be enabled/disabled independently of writes.**
  Previously `MCP_ALLOW_JOB_WRITE` gated creation, update, and deletion together,
  so there was no way to let an agent create jobs but never delete them. Job
  deletion, job update, build stop, queue cancellation, and node offlining are now
  separately gated:
  - `MCP_ALLOW_DESTRUCTIVE` (chart: `mcp.allowDestructive`, default `true`) — master
    switch that disables all of them at once.
  - `MCP_ALLOW_JOB_DELETE` (chart: `mcp.allowJobDelete`, default `false`).
  - `MCP_ALLOW_JOB_UPDATE` (chart: `mcp.allowJobUpdate`, default `true`).
  - `MCP_ALLOW_BUILD_STOP` (chart: `mcp.allowBuildStop`, default `true`) — also
    covers queue cancellation, and does not affect triggering builds.

  Each action must clear its category flag, the master switch, and its own flag.
  `MCP_READ_ONLY` and `MCP_ALLOWED_JOBS` continue to take precedence.
- The chart can now optionally create the `SecretStore`/`ClusterSecretStore` as well
  as the `ExternalSecret`, via `externalSecret.secretStore.create` with a
  pass-through `provider` block, so a deployment no longer requires the store to be
  provisioned separately.
- `examples/values/external-secrets-gcp-workload-identity.yaml`: a complete GCP
  Secret Manager example that creates the `ClusterSecretStore` using Workload
  Identity, annotates the Kubernetes service account with
  `iam.gke.io/gcp-service-account`, and pins `fullnameOverride` so the SA name
  matches the Workload Identity binding. Rendered in CI on every build.
- `externalSecret` gained `apiVersion` (for ESO installs still on `v1beta1`),
  `creationPolicy`, `deletionPolicy`, `annotations`, `usernameRemoteProperty` /
  `tokenRemoteProperty` (for JSON secrets addressed by field), `remoteVersion`,
  `extraData`, `dataFrom`, and `template`.

### Fixed

- Enabling `externalSecret.enabled` and `jenkins.credentials.create` together
  rendered two resources both owning a Secret named `<fullname>-credentials`, so
  External Secrets and Helm would fight over it. This combination now fails the
  render with an explanatory message.
- `externalSecret.secretStore.create=true` with an empty `provider` rendered an
  invalid store that the API server would reject confusingly; Helm's `required`
  does not catch empty maps. It now fails the render.
- Creating a `SecretStore` while `secretStoreRef.kind` said `ClusterSecretStore`
  (or vice versa) produced an ExternalSecret pointing at a store that does not
  exist. The mismatch now fails the render.
- A `ClusterSecretStore` using the `gcpsm` provider now fails the render unless
  `auth.workloadIdentity.serviceAccountRef.namespace` (or, for key-file auth,
  `auth.secretRef.secretAccessKeySecretRef.namespace`) is set. Cluster-scoped
  stores have no namespace of their own, so External Secrets cannot resolve the
  reference without it — previously this rendered cleanly and failed only at
  runtime. Namespaced `SecretStore` resources are unaffected, where the namespace
  is optional.

### Changed

- **Behaviour change:** `delete_job` is refused by default. Set
  `MCP_ALLOW_JOB_DELETE=true` (or `mcp.allowJobDelete: true`) to restore the
  previous behaviour. This matches the chart's existing safe-by-default stance for
  `allowNodeWrite` and `allowAdminRequest`.

## [1.3.0] - 2026-08-02

### Security

- **Fixed an allowlist bypass via path traversal in job names.** `Policy.check_job`
  matched the raw job name against `MCP_ALLOWED_JOBS` globs while `_job_path` built
  the URL without rejecting `.` and `..` segments. A name such as
  `AI/../Production/secret` matched an `AI/*` allowlist but resolved to a different
  Jenkins path once `..` was normalised away, and a bare `..` escaped the job
  namespace entirely (permitted by the default `MCP_ALLOWED_JOBS: "*"`). Traversal
  segments are now rejected at both the policy layer and the URL-building layer.
- `jenkins_admin_request` now rejects protocol-relative paths (`//host/path`) and
  paths containing `.` or `..` segments.

### Fixed

- Truncated console log pages no longer skip data. When a response exceeded
  `MCP_MAX_LOG_BYTES`, `next_start` returned Jenkins' `X-Text-Size` rather than the
  offset actually delivered, so the following page silently omitted the clipped
  bytes. Truncated pages now resume from the delivered offset and report
  `more_data: true`.
- A stale CSRF crumb is reissued once and the request retried, instead of surfacing
  an unrecoverable 403. The refresh does not consume the configured retry budget, so
  it works with `JENKINS_MAX_RETRIES=0`. Unrelated 403s (for example missing
  permissions) do not trigger a retry.

### Changed

- Declared `jsonschema` as an explicit dev dependency; it was used directly by
  `tests/test_manifests.py` but only reached the environment transitively via `mcp`.
- CI now runs `mypy src/` and `scripts/validate_manifests.py`, matching the existing
  Makefile targets.
- The Helm CI job pins Python via `actions/setup-python` rather than relying on the
  runner's default interpreter for the version-consistency check.
- Bumped `actions/dependency-review-action` from v4 to v5 (Node 24 runtime).
- The MCP `initialize` response now advertises the application version in
  `serverInfo.version`. It previously reported the MCP SDK's own version, so clients
  could not tell which build of the server they were connected to.

## [1.2.0] - 2026-08-01

### Added

- Production Helm chart with Tailscale ingress and egress integration.
- Optional Tailscale `ProxyGroup` resources and External Secrets support.
- GitHub Actions for Python CI, Docker multi-architecture publishing, Helm OCI publishing, integration tests, CodeQL, and dependency auditing.
- Synchronized application, package, Kustomize, image, and chart version tooling.
- Argo CD examples for Git and GHCR OCI chart sources.
- GitHub bootstrap and release documentation for `grglzrv/jenkins-mcp-server`.

### Changed

- Standardized package and image name to `jenkins-mcp-server`.
- Added OCI metadata and image provenance/SBOM generation.
- Hardened Helm defaults for non-root, read-only execution and least-privilege MCP capabilities.

## [1.1.0] - 2026-08-01

- Added Kubernetes, Tailscale, Argo CD, health checks, and deployment hardening.

## [1.0.0] - 2026-08-01

- Initial production-focused rewrite of the original MIT-licensed Jenkins MCP package.
