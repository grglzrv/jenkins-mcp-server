# Changelog

All notable changes are documented here. The project follows Semantic Versioning
and uses the categories below for every release. Empty categories are retained
with an explicit `None` so compatibility, security, upgrade impact, and known
limitations are never left ambiguous. GitHub Release notes are rendered directly
from the matching version entry after CI validates it.

## [Unreleased]

### Highlights

- None.

### New Features

- None.

### Improvements

- None.

### Bug Fixes

- None.

### Breaking Changes

- None.

### Known Issues

- None.

### Security

- None.

### Upgrade Notes

- No action required.

## [2.6.2] - 2026-08-09

### Highlights

- `mcp.extraEnv` can no longer override the server credentials/policy or
  Minibridge tool policy and authentication that the chart validated.

### New Features

- None.

### Improvements

- `mcp.extraEnv` rejects chart-owned names in any capitalisation, so a spelling
  that would have had no effect is reported instead of shipped.
- Regression coverage derives the protected surface from every application
  alias and Minibridge variable emitted by the chart, preventing future names
  from silently falling outside the guard.
- Reframed onboarding as an operator guide and removed repository text that
  issued installation and credential-gathering instructions directly to AI
  agents when they merely read the README.

### Bug Fixes

- Settings were read case-insensitively, which is the pydantic-settings default.
  `mcp.extraEnv` blocked only the uppercase spellings, so an entry named
  `jenkins_token` passed the chart guard and then replaced the token supplied by
  the Secret. Settings are now matched case-sensitively. Documented uppercase
  names are unaffected.
- Minibridge's unprefixed `TOOLS_DENY`, `TOOLS_ALLOW`, `METHODS_DENY`,
  `GUARDRAILS`, and `BASIC_AUTH_SECRET` variables were not covered by the
  prefix-based chart guard. Because `mcp.extraEnv` renders last, their canonical
  spellings could replace the chart-owned tool policy, guardrails, or basic-auth
  secret. They are now reserved explicitly in any capitalisation.

### Breaking Changes

- None for documented configuration. A deployment that relied on a lowercase
  environment variable being read must rename it to the documented uppercase
  spelling.

### Known Issues

- None.

### Security

- Closes a bypass of the 2.6.0 control that prevents Helm values replacing
  Jenkins credentials or security policy. `mcp.extraEnv` entries such as
  `jenkins_token`, `mcp_read_only` and `mcp_allowed_jobs` were accepted and took
  effect at runtime, so an operator with values access could substitute
  credentials or disable the job allowlist without touching the Secret. The fix
  is applied in the server, which decides, and in the chart, which reports.
- Closes the equivalent Minibridge bypass that could weaken proxy tool policy or
  replace its basic-auth secret through a later `mcp.extraEnv` entry.
- Removes agent-targeted repository instructions that could be interpreted as
  prompt injection by automated reviewers or repository-reading assistants.

### Upgrade Notes

- If a values file sets a lowercase chart-owned name in `mcp.extraEnv`, the
  render now fails and names the entry. Remove it and use the corresponding
  `jenkins.*`, `mcp.*`, `audit.*` or `minibridge.*` value.
- Direct Docker and raw-manifest users must use the documented uppercase
  application setting names; lowercase or mixed-case spellings are ignored.

## [2.6.1] - 2026-08-09

### Highlights

- Helm installation now leads with the production existing-Secret path and a
  normal external Jenkins URL, while detailed operations stay in focused docs.

### New Features

- None.

### Improvements

- The main README now keeps Helm installation and troubleshooting concise and
  links to the chart and troubleshooting guides as their authoritative sources.
- Health responses are explicitly non-cacheable JSON and carry
  `X-Content-Type-Options: nosniff`.

### Bug Fixes

- A Jenkins permission 403 no longer suggests CSRF crumb or proxy changes unless
  the controller response actually mentions a crumb.
- The main Helm command no longer combines an external-Jenkins Secret with the
  Tailscale production values, and non-Tailscale values examples no longer use
  misleading MagicDNS hostnames.

### Breaking Changes

- None.

### Known Issues

- None.

### Security

- Readiness state cannot be cached by an intermediary or MIME-sniffed as a
  different content type.

### Upgrade Notes

- No action is required. Existing installations and values remain compatible.

## [2.6.0] - 2026-08-09

### Highlights

- Jenkins API, job-config, and crumb responses are now streamed through a
  configurable 10 MB safety bound instead of being buffered without a limit.

### New Features

- `mcp.maxResponseBytes` (`MCP_MAX_RESPONSE_BYTES`, default 10 MB) limits every
  complete Jenkins response independently of the smaller, truncating console
  and administrator-response limit.

### Improvements

- HTTP errors now give targeted hints for authentication failures, permission
  or crumb failures, and unexpected proxy/SSO redirects.
- A new troubleshooting guide covers health semantics, external Jenkins DNS,
  firewalls and egress policies, TLS, redirects, layered tool policy, audit
  degradation, and multi-replica session affinity.
- The main and chart READMEs, onboarding, compatibility documentation, every
  Helm and Argo CD example, raw manifests, standalone Minibridge deployment,
  and k3s smoke values document the same operational limits and diagnostics.
- The live credential smoke matrix now covers split username/token Secret refs
  and verifies that existing-Secret rotation takes effect after the documented
  Deployment restart without transferring Secret ownership to Helm.

### Bug Fixes

- Jenkins JSON, XML, and crumb responses could consume unbounded process memory.
  Oversized complete responses now fail predictably, while console/admin text
  retains its documented bounded-truncation behavior.
- Malformed Jenkins API JSON leaked a decoder exception. It is now reported as
  a stable `JenkinsError` naming the affected endpoint.
- Troubleshooting incorrectly said TLS verification failures occurred at
  startup and that server-side `mcp.allow*` flags removed tools from discovery.
  TLS is exercised on Jenkins calls; only Minibridge tool policy filters
  `tools/list`, while server policy rejects unauthorized calls.
- `mcp.extraEnv` could silently override the validated Jenkins credential
  source or chart-owned policy/proxy variables because explicit environment
  entries take precedence over `envFrom`. Reserved names and duplicates now
  fail the chart render.

### Breaking Changes

- `mcp.extraEnv` no longer accepts chart-owned `JENKINS_*`, `MCP_*`, or
  `MINIBRIDGE_*` names, `OTEL_EXPORTER_OTLP_ENDPOINT`, or duplicate names.
  These entries previously overrode the chart's validated values silently.

### Known Issues

- None.

### Security

- Bounded upstream responses reduce memory-exhaustion risk from unexpectedly
  large or intermediary-generated Jenkins payloads.
- Helm values can no longer replace Jenkins credentials or security policy
  through a duplicate `mcp.extraEnv` entry after passing source validation.

### Upgrade Notes

- No action is required. If a measured legitimate Jenkins API or job-config
  response exceeds 10 MB, raise `mcp.maxResponseBytes` deliberately; prefer a
  narrower folder/query where possible.
- Review `mcp.extraEnv` before upgrading. Move any chart-owned variable to its
  corresponding `jenkins.*`, `mcp.*`, `audit.*`, or `minibridge.*` value;
  ordinary extras such as `HTTP_PROXY`, `NO_PROXY`, and `SSL_CERT_FILE` remain
  supported.

## [2.5.0] - 2026-08-09

### Highlights

- An unwritable audit file no longer removes every replica from service.

### New Features

- `audit.requiredForReadiness` (`MCP_AUDIT_REQUIRED_FOR_READINESS`, default
  `false`) makes a writable audit file a readiness condition, for deployments
  where the file is the record of account rather than a redundant copy.
- Optional audit-file output supports bounded size rotation through
  `audit.maxFileBytes` / `MCP_AUDIT_MAX_BYTES` and `audit.backupCount` /
  `MCP_AUDIT_BACKUP_COUNT`. The chart retains an active 50Mi file and three
  backups by default, below its 256Mi `emptyDir` limit.

### Improvements

- `/readyz` reports `audit_log_writable` and, when it fails, `audit_log_error`
  whether or not the check is required, so the problem stays visible either way.
- A failed audit path is actively re-probed by readiness until it recovers,
  even when no Jenkins action occurs. Error details stay in process logs while
  `/readyz` exposes only the exception class, not internal filesystem paths.

### Bug Fixes

- A failed audit-file write took the pod out of service. Records also go to the
  process logs, which the chart already documents as the durable path, so the
  file is a redundant copy. Failing readiness on it removed every replica at
  once: a shared PVC is one volume, and identically sized `emptyDir` volumes can
  fail together under similar load. That turned a recoverable disk condition
  into a total outage while the process-log audit trail itself was intact.

### Breaking Changes

- The default readiness behavior changes for deployments that enable audit-file
  output: a failed redundant copy is reported but does not return 503. Set
  `audit.requiredForReadiness: true` before upgrading when the file must fail
  closed.

### Known Issues

- Rotation is size-based and does not compress backups. Size the configured
  file and backup count for the required retention, or collect process logs.

### Security

- Audit records are unaffected: they are written to the process logs before the
  file, so nothing that was recorded before is lost now. Deployments that treat
  the file as the record of account should set `audit.requiredForReadiness:
  true` to keep failing closed.
- Audit files and rotation locks are created with owner-only permissions, and
  readiness no longer returns raw operating-system error strings.

### Upgrade Notes

- Existing file-audit deployments that require fail-closed behavior must set
  `audit.requiredForReadiness: true`. Set both rotation values to zero only when
  an external rotation mechanism owns retention.

## [2.4.2] - 2026-08-09

### Highlights

- Jenkins writes are never replayed after an HTTP response or an ambiguous
  transport failure, job allowlists cover reads and queue cancellation, and
  response limits now bound downloaded data rather than only returned text.

### New Features

- None.

### Improvements

- Concurrent writes now share a single crumb request. Eight parallel triggers
  previously issued eight requests to the crumb issuer, which is often the
  slowest part of a controller.
- Crumb issuer failures raise `JenkinsError` with a message naming the status,
  instead of leaking `httpx.HTTPStatusError` through tools that document only
  `JenkinsError`.
- Optional audit-file writes run outside the async event loop. A failed file
  destination degrades `/readyz`, records continue in the process logs, and
  readiness recovers after the destination becomes writable again.
- Safe read retries honour a bounded `Retry-After` delay and otherwise use
  jittered backoff so replicas do not retry in lockstep. Configuration now
  rejects malformed Jenkins URLs, invalid ports, negative retry counts, and
  invalid response limits at startup.
- Bringing a node online requires node-write permission but no longer requires
  the destructive-action master switch used for taking it offline.

### Bug Fixes

- `trigger_build`, `delete_job`, `stop_build` and every other write were
  replayed on 429, 502, 503, 504 and on read timeouts. None of those responses
  proves whether Jenkins acted on the first attempt, and Jenkins has no
  idempotency key, so a replay could queue a second build while the tool
  reported a single success. Writes now retry only failures that occur before
  sending: connection establishment, connection timeout, or pool acquisition.
- `MCP_ALLOWED_JOBS` was not applied to job listings, the queue, or running
  builds, and `cancel_queue_item` could cancel an item belonging to a job
  outside the allowlist. Results are filtered now, and cancellation resolves
  and authorizes the queued job before sending the mutation.
- Arbitrary string build numbers were interpolated into URLs. Build tools now
  accept only positive numbers and documented Jenkins aliases, preventing path,
  query, and fragment injection.
- `MCP_MAX_LOG_BYTES` sliced console and administrator responses only after
  HTTPX buffered the complete body. Those endpoints now stop streaming at the
  configured limit, preventing large responses from exhausting pod memory,
  without attempting to decode compressed Jenkins responses twice.
- Copying a job to a nested target sent the complete target path as a root job
  name. The copy is now created through its parent folder endpoint.
- An unwritable audit path no longer fails the tool call. `emit` runs after the
  Jenkins request has completed, so raising reported a failure for an action
  that had already succeeded, inviting the caller to repeat it. The record
  remains in the process logs and the path problem is reflected in readiness.

### Breaking Changes

- None. Writes fail faster on ambiguous transient failures, which is the
  intended correction: the previous behaviour hid duplicates rather than
  preventing them.

### Known Issues

- None.

### Security

- Job allowlists now cover job discovery, queue visibility, running-build
  visibility, build selectors, and queue cancellation as documented.

### Upgrade Notes

- No action required.

## [2.4.1] - 2026-08-09

### Highlights

- Restored NetworkPolicy to an explicit opt-in so firewall-protected external
  Jenkins URLs remain reachable without broad or brittle CIDR rules.

### New Features

- None.

### Improvements

- Moved the raw Tailscale-specific NetworkPolicy out of the neutral Kubernetes
  base and into the Tailscale overlay.
- Updated every Helm values example and Argo CD Application to state whether
  NetworkPolicy is intentionally disabled for external Jenkins or enabled for
  the Tailscale proxy path.
- Added Helm NOTES for the disabled-policy boundary and for health ports
  published by `LoadBalancer` or `NodePort` Services.
- Clarified that the legacy `allowInternetEgress` switch permits unrestricted
  egress, because Kubernetes NetworkPolicy cannot identify public destinations
  or external DNS names portably.

### Bug Fixes

- Changed `networkPolicy.enabled` back to `false`; the 2.4.0 default-deny egress
  policy blocked direct external Jenkins hostnames unless operators opened all
  egress or maintained destination CIDRs.
- Removed the unconditional NetworkPolicy from the standalone Minibridge raw
  manifest, which otherwise imposed a different networking default from Helm.
- Removed a stale MagicDNS instruction from the environment-neutral raw
  Kubernetes configuration.

### Breaking Changes

- None.

### Known Issues

- Kubernetes NetworkPolicy has no portable DNS-name destination selector. When
  enabling it for direct external Jenkins, use stable `ipBlock` rules, a
  selectable in-cluster proxy, or the unrestricted egress opt-in.

### Security

- Network isolation remains available and fully tested as an explicit opt-in.
  Deployments that rely on a firewall allowing only authorized pods or clusters
  no longer need to weaken a default-deny policy merely to reach Jenkins.

### Upgrade Notes

- Upgrading from 2.4.0 removes the chart-managed NetworkPolicy unless
  `networkPolicy.enabled=true` is set explicitly. Set it before upgrading when
  the 2.4.0 policy is part of the intended security boundary.
- Raw Tailscale users continue to receive the policy through the production
  overlay. The neutral base and standalone Minibridge manifest no longer apply
  one automatically.

## [2.4.0] - 2026-08-09

### Highlights

- Safer deployment defaults make destructive Jenkins operations, unrestricted
  network reachability, and unrotated audit files explicit opt-ins.

### New Features

- Added `networkPolicy.allowSameNamespace` so the default-deny policy can keep
  same-namespace clients working without opening MCP to every namespace.
- Added a bounded `audit.storage.emptyDir.sizeLimit` value, defaulting to
  `256Mi`, for installations that opt back into file audit logging.

### Improvements

- Enabled NetworkPolicy by default with DNS, Helm-test health traffic,
  same-namespace MCP ingress, and enabled Tailscale egress modeled explicitly.
- Updated all Helm values examples and Argo CD Applications to state their
  destructive-action, audit, client-ingress, and Jenkins-egress intent.
- Updated the main README, chart README, security guide, onboarding guide,
  examples guide, Tailscale/Argo CD guide, Compose stack, and raw Kubernetes
  manifests for the new defaults and migration path.
- Added a NetworkPolicy to the standalone Minibridge manifest and removed the
  unused audit data mount from the raw Deployment and Compose defaults.

### Bug Fixes

- Corrected the GCP Workload Identity example, whose comment said destructive
  actions were disabled while its value enabled them.
- Prevented fresh runtime and chart deployments from enabling job updates,
  build stops, queue cancellation, or node offlining through an implicit master
  switch.
- Avoided unbounded file growth on default `emptyDir` and Compose volumes; audit
  JSONL continues to be emitted to stdout.

### Breaking Changes

- `MCP_ALLOW_DESTRUCTIVE` and `mcp.allowDestructive` now default to `false`.
- `networkPolicy.enabled` now defaults to `true`; clients outside the release
  namespace and non-Tailscale Jenkins egress require explicit rules.
- `audit.fileEnabled` now defaults to `false`; file consumers must opt in and
  provide rotated or bounded storage.

### Known Issues

- Kubernetes NetworkPolicy cannot select an external Jenkins endpoint by DNS
  name. Use a stable `ipBlock`, a private-network proxy such as the documented
  Tailscale egress Service, or the broader `allowInternetEgress` opt-in.

### Security

- Irreversible Jenkins actions now fail closed unless the master switch and the
  corresponding category/action gates are all enabled.
- MCP ingress and server egress are isolated by default; same-namespace client
  access is the only general ingress allowance.
- Stdout is now the default audit sink, avoiding a silently growing local file.

### Upgrade Notes

- Before upgrading from 2.3, explicitly set `mcp.allowDestructive=true` only if
  existing automation genuinely needs irreversible operations.
- Add client namespaces to `networkPolicy.allowedNamespaces`; configure
  `additionalEgress`, Tailscale egress, or `allowInternetEgress=true` for the
  Jenkins endpoint. Set `allowSameNamespace=false` for a dedicated server
  namespace.
- Set `audit.fileEnabled=true` only when a file consumer is required, and retain
  the 256Mi emptyDir bound or supply a PVC with external rotation.

## [2.3.3] - 2026-08-09

### Highlights

- Helm rendering and values validation are hardened so malformed manifests and
  unsafe credential aliases fail before they reach the Kubernetes API.

### New Features

- Added strict kubeconform validation for the default chart and every shipped
  values example to both pull-request and release gates.

### Improvements

- Added typed nested value schemas, valid Kubernetes/ESO enums, probe ranges,
  Tailscale hostname validation, and the Jenkins chart icon.
- Made the Helm test image and pull policy configurable for private registries
  and mirrored/offline clusters.
- Quoted user-controlled Kubernetes strings consistently, made Service affinity
  rendering explicit, and corrected TLS-aware endpoints in Helm notes.
- Updated the main README, chart README, values reference, examples guide, and
  raw credential manifests with the hardened configuration contracts.

### Bug Fixes

- Fixed the Helm test Pod's duplicate `app.kubernetes.io/component` YAML key,
  which strict YAML parsers rejected.
- Reject credential mappings that alias the Jenkins user ID and token, duplicate
  ExternalSecret target keys, reuse Jenkins credentials for Minibridge auth, or
  select invalid External Secrets creation/deletion policy combinations.
- Reject unsupported `ExternalName` chart Services, listener/Service port
  collisions, inverted HPA replica ranges, empty PodDisruptionBudgets, invalid
  Tailscale ingress/egress combinations, and reserved pod metadata overrides.
- Preserve string values such as `on` in rendered Secret, ingress, Service,
  audit, and Tailscale fields instead of letting YAML coerce them to booleans.

### Breaking Changes

- None for valid configurations. Previously accepted invalid or silently ignored
  combinations now fail at Helm validation time with an actionable message.

### Known Issues

- NetworkPolicy and file audit-log defaults remain unchanged in this patch; the
  2.4.0 release changes those security defaults with documented migration notes.

### Security

- Prevented Jenkins API credentials from being accidentally reused as client or
  remote-policer authentication material.
- Made schema typos and ambiguous credential source mappings fail closed.

### Upgrade Notes

- Run `helm lint` with your production values before upgrading. Correct any
  newly rejected nested typos, duplicate Secret-key mappings, invalid ESO policy
  pair, port collision, or HPA/PDB invariant; valid 2.3.2 values need no change.

## [2.3.2] - 2026-08-09

### Highlights

- Credential source contracts are now explicit and guarded consistently across
  chart-managed, existing-Secret, and External Secrets deployments.

### New Features

- None. This patch hardens and documents existing credential sources.

### Improvements

- Made both External Secrets target keys explicit in the live ESO rotation
  smoke test, matching the shipped values examples.
- Added regression coverage requiring all enabled chart-managed and External
  Secrets examples to state their complete credential field set.
- Documented the four-field External Secrets mapping in the main README, chart
  README, examples guide, values reference, and JSON schema.
- Quoted user-controlled target and remote key names in rendered Secret and
  ExternalSecret manifests.

### Bug Fixes

- Reject existing-Secret or External Secrets configurations that map the
  Jenkins user ID and API token to the same target Secret key.
- Require both remote object names while rendering the explicit ExternalSecret
  data mapping, in addition to schema validation.

### Breaking Changes

- None. Valid 2.x credential configurations and existing defaults are
  unchanged.

### Known Issues

- The historical default `externalSecret.usernameRemoteKey` remains
  `jenkins-mcp-username` for 2.x compatibility. Copied values should set both
  remote object names explicitly; shipped examples use
  `jenkins-mcp-user-id`.

### Security

- No credential values were added. Earlier validation now prevents one Secret
  value from being wired into both authentication fields accidentally.

### Upgrade Notes

- No values migration is required from 2.3.0. Version 2.3.1 was not published;
  its documented example updates first shipped in 2.3.2. A release using the
  same target key for both fields must correct that invalid mapping before
  upgrading.

## [2.3.1] - 2026-08-08

> This version was not published or tagged. The changes below first shipped in
> 2.3.2.

### Highlights

- Existing-Secret examples now show the complete Secret reference contract:
  Secret name, Jenkins user ID key, and API token key.

### New Features

- None. This patch changes examples, documentation, and regression coverage.

### Improvements

- Made `usernameKey` and `tokenKey` explicit in every enabled
  `existingSecret` example, including Argo CD, production, Minibridge,
  onboarding, and CI smoke values.
- Clarified across the main README, chart README, examples guide, and security
  guide that production values should state all three existing-Secret fields.

### Bug Fixes

- Removed inconsistent reliance on implicit existing-Secret key defaults from
  shipped examples.
- Documented that raw Kubernetes manifests use the fixed environment keys
  `JENKINS_USERNAME` and `JENKINS_TOKEN`; Helm key overrides do not apply to
  those manifests.

### Breaking Changes

- None. Chart defaults and runtime Secret keys are unchanged.

### Known Issues

- Raw-manifest users must retain the fixed `JENKINS_USERNAME` and
  `JENKINS_TOKEN` keys, while Helm users may reference custom key names through
  `existingSecret.usernameKey` and `existingSecret.tokenKey`.

### Security

- No secret values were added. Examples continue to contain placeholders and
  reference Kubernetes Secrets rather than embedding credentials in workloads.

### Upgrade Notes

- No values migration is required from 2.3.0. Existing installations keep the
  same defaults; copying a refreshed example simply makes both key names
  explicit.

## [2.3.0] - 2026-08-08

### Highlights

- Chart-created credentials now ask for a Jenkins user ID and its matching API
  token using purpose-based value names rather than internal Secret key names.

### New Features

- Added `create.jenkinsUserId` and `create.jenkinsApiToken` as the canonical
  chart-managed credential fields.

### Improvements

- Documented LDAP identity semantics at every credential entry point: use the
  actual value that replaces `{0}` in Jenkins' configured User search filter
  (`uid` for the common `uid={0}` filter), rather than assuming a universal ID
  format, email address, or display name.
- Clarified that the API token must be generated by the same Jenkins user ID.
- Added CI renders that compare canonical credentials with deprecated 2.2 and
  2.1 inputs, preserving the major-version-2 upgrade contract.

### Bug Fixes

- The values interface no longer exposes `JENKINS_USERNAME` and `JENKINS_TOKEN`
  as if Kubernetes Secret keys were the clearest customer-facing terminology.
- Updated the shipped Hermes Agent configs to its current HTTP MCP schema:
  `mcp_servers.<name>.url` with `timeout`, without unsupported `transport` or
  `timeout_seconds` fields.

### Breaking Changes

- None. The 2.2 uppercase fields and 2.1 lowercase fields remain accepted as
  deprecated compatibility inputs throughout major version 2.

### Known Issues

- The concrete LDAP identifier depends on the Jenkins `userSearch`
  configuration; operators must supply the value their controller resolves as
  the login ID.

### Security

- Credential storage behavior is unchanged: chart-managed values remain in
  Helm release history, while existing Secret and External Secrets sources
  remain available for production secret lifecycle management.

### Upgrade Notes

- New values should use `create.jenkinsUserId` and
  `create.jenkinsApiToken`. Existing 2.1 and 2.2 chart-managed values continue
  to render the same workload Secret and can be migrated independently.

## [2.2.0] - 2026-08-08

### Highlights

- Helm's chart-created credential values now use the exact Kubernetes Secret
  keys `JENKINS_USERNAME` and `JENKINS_TOKEN`, while the normal production path
  is clearly the single `existingSecret` reference.

### New Features

- Added first-class `create.JENKINS_USERNAME` and `create.JENKINS_TOKEN` values
  with render-time validation that both are supplied.

### Improvements

- Moved `create` to the top of the credential values and documented its required
  fields at the point of use.
- Clarified that `existingSecret` handles one Secret containing both values,
  while `secretKeyRefs` is an advanced option for values split across different
  Secret objects.
- Replaced the unsupported `mcp_servers.jenkins` examples with portable client
  connection details: name, Streamable HTTP transport, and `/mcp` URL.

### Bug Fixes

- Helm notes and deployment documentation no longer imply that all MCP clients
  accept a repository-defined configuration schema.

### Breaking Changes

- None. Deprecated 2.1.x `create.username`, `create.token`, `usernameKey`, and
  `tokenKey` values remain accepted throughout major version 2.

### Known Issues

- Client configuration key names remain client-specific; use the chosen MCP
  client's documentation around the supplied transport and endpoint.

### Security

- Empty chart-managed credentials fail rendering instead of allowing a
  plausible placeholder credential to deploy.

### Upgrade Notes

- New configurations should use `create.JENKINS_USERNAME` and
  `create.JENKINS_TOKEN`. Existing 2.1.x chart-managed values continue to work
  and can be migrated without changing the Secret consumed by the workload.

## [2.1.1] - 2026-08-08

### Highlights

- Chart-managed credentials now expose only the real username and token values
  in normal configuration; Secret key names are stable implementation details.

### New Features

- None.

### Improvements

- `create.usernameKey` and `create.tokenKey` are removed from default values and
  deprecated in the schema. Existing 2.x overrides remain supported, while new
  installs use `JENKINS_USERNAME` and `JENKINS_TOKEN` directly.
- Every k3s smoke waits for a node object to exist before waiting for readiness,
  removing a runner startup race that could fail a release before tests began.

### Bug Fixes

- The values file no longer labels the disabled `existingSecret` source as the
  installation default; it is now described as the production recommendation.

### Breaking Changes

- None.

### Known Issues

- None.

### Security

- Chart-managed tokens remain stored in Helm release history; use an existing
  Secret or External Secrets Operator for production credentials.

### Upgrade Notes

- No action is required. Existing custom chart-managed key overrides continue
  to work in 2.x, but new configurations should omit them.

## [2.1.0] - 2026-08-08

### Highlights

- The chart creates the credentials Secret by default, so a first install needs
  only a Jenkins URL, username and token, and credential delivery is verified
  end to end for Helm-managed, existing Secret, and External Secrets Operator
  modes.

### New Features

- CI installs External Secrets Operator 2.8.0 and exercises its Kubernetes
  provider against an isolated source namespace, including credential
  rotation.

### Improvements

- `jenkins.credentials.create.enabled` defaults to `true` and
  `existingSecret.enabled` to `false`. Installing without credentials now fails
  at render naming the missing value, instead of deploying a pod that waits
  indefinitely for a Secret nobody created.
- Each credential source now owns its target key names instead of reusing the
  disabled `existingSecret` source's settings.
- Helm-managed credential changes add a pod-template checksum and roll the
  Deployment so the process receives rotated values.
- Credential ownership, rotation, and restart behavior is documented in the
  project README, chart README, onboarding, security guide, examples, and raw
  deployment manifests.

### Bug Fixes

- CI and release renders now supply both values required by the default
  chart-managed source.
- The two ESO examples no longer contain duplicate `enabled` keys, and the
  chart-managed example uses the current nested token path.
- `externalSecret.extraData` can no longer be silently ignored when `dataFrom`
  is configured; the chart rejects the ambiguous combination.

### Breaking Changes

- Any values file selecting a source other than `create` must now disable it,
  because exactly one source may be enabled:

  ```yaml
  jenkins:
    credentials:
      create:
        enabled: false
      existingSecret:
        enabled: true
        name: jenkins-mcp-secrets
  ```

  Every shipped example and Argo CD application is updated accordingly.

### Known Issues

- Enabling any non-default source requires explicitly disabling `create`, which
  is noisier than it should be. A single `jenkins.credentials.source` enum would
  express the choice in one value and is worth considering before the next
  major.

### Security

- The default now stores the Jenkins API token in the Helm release, readable by
  anyone who can run `helm get values` on the namespace. That is an acceptable
  trade for a first install and is documented as such, but `existingSecret` or
  `externalSecret` remain the right choice for anything longer-lived.
- Existing and ESO-managed credentials remain outside Helm release history;
  the smoke suite proves that Helm uninstall preserves an existing Secret and
  that ESO performs the target synchronization.

### Upgrade Notes

- If you set `existingSecret`, `secretKeyRefs` or `externalSecret`, add
  `create: { enabled: false }` alongside it. No action is required with the
  default key names. If a chart-managed Secret uses custom keys, set
  `create.usernameKey` and `create.tokenKey`. For ESO, use
  `externalSecret.targetUsernameKey` and `targetTokenKey`.

## [2.0.0] - 2026-08-08

### Highlights

- The credential and NetworkPolicy values are restructured for production use.
  Both are breaking; see Upgrade Notes.

### New Features

- `jenkins.credentials.secretKeyRefs` reads the username and token from
  different Secrets or keys, replacing the unlabelled `valueFrom` block.

### Improvements

- Each credential source is an explicit `enabled` flag under
  `jenkins.credentials`: `existingSecret`, `secretKeyRefs`, `create` and
  `externalSecret`. Exactly one may be enabled. Enabling none fails with the
  list of options; enabling several fails naming the count and the conflict,
  replacing six pairwise checks that each described one arbitrary pair.
- `existingSecret` carries its own `name`, `usernameKey` and `tokenKey`, so the
  key names sit with the Secret they belong to.
- The whole External Secrets configuration — store reference, optional store
  creation, remote keys and properties, version pin, policies, extra data,
  `dataFrom` and `template` — moves under `jenkins.credentials.externalSecret`.
  All four sources are now configured in one place.

### Bug Fixes

- None.

### Breaking Changes

- `jenkins.credentials.existingSecret` is an object, not a string.
- `jenkins.credentials.create` is an object with `enabled`, `username` and
  `token`, not a boolean beside two sibling fields.
- `jenkins.credentials.usernameKey` and `tokenKey` moved under `existingSecret`.
- `jenkins.credentials.valueFrom` is now `jenkins.credentials.secretKeyRefs`
  with an `enabled` flag.
- The top-level `externalSecret` key no longer exists. Every field beneath it
  moves under `jenkins.credentials.externalSecret`, unrenamed.
- `networkPolicy.enabled` defaults to `false`.
- `networkPolicy.tailscaleNamespace`, `hermesNamespace` and
  `allowDirectHermesAccess` are replaced by `networkPolicy.allowedNamespaces`,
  a list. The policy no longer assumes a Tailscale or agent namespace exists.

### Known Issues

- None.

### Security

- The NetworkPolicy previously defaulted to enabled while selecting peers by
  namespace labels that differ per cluster. A policy that does not match the
  cluster silently blocks traffic instead of failing, which is a poor default
  for a control people rely on. It is now opt-in, and the namespaces it admits
  are stated explicitly rather than assumed.

### Upgrade Notes

- Credentials, from the most common form:

  ```yaml
  # before
  jenkins:
    credentials:
      existingSecret: jenkins-mcp-secrets
      usernameKey: JENKINS_USERNAME
      tokenKey: JENKINS_TOKEN
      create: false

  # after
  jenkins:
    credentials:
      existingSecret:
        enabled: true
        name: jenkins-mcp-secrets
        usernameKey: JENKINS_USERNAME
        tokenKey: JENKINS_TOKEN
  ```

- External Secrets: indent the existing block under `jenkins.credentials` and
  disable the default source. No field is renamed.

  ```yaml
  # before
  externalSecret:
    enabled: true
    secretStoreRef:
      name: gcp-secret-store
      kind: ClusterSecretStore

  # after
  jenkins:
    credentials:
      existingSecret:
        enabled: false
      externalSecret:
        enabled: true
        secretStoreRef:
          name: gcp-secret-store
          kind: ClusterSecretStore
  ```

- If you relied on the NetworkPolicy, set `networkPolicy.enabled: true` and list
  the namespaces that may reach the server in `networkPolicy.allowedNamespaces`.

## [1.27.1] - 2026-08-07

### Highlights

- Every shipped Kubernetes deployment path now makes stateful MCP routing and
  the Minibridge AIO HTTP-to-stdio boundary explicit.

### New Features

- Mermaid topology diagrams show clients using public Streamable HTTP at
  `/mcp` while Minibridge privately launches Jenkins MCP Server over stdio.

### Improvements

- Raw Kubernetes resources now consistently use
  `app.kubernetes.io/component: mcp-server` without changing immutable
  selectors.
- Production values and all chart-based Argo CD Applications explicitly carry
  the 600-second `ClientIP` session-affinity contract.

### Bug Fixes

- Raw Kubernetes and standalone Minibridge Services now keep requests in one
  Streamable HTTP session on the replica that initialized it.

### Breaking Changes

- None. Existing selectors, endpoints, and client transports are unchanged.

### Known Issues

- Service affinity cannot migrate in-memory sessions after a pod restart, and
  ingress controllers that bypass Service balancing or mask source addresses
  may need their own affinity configuration.

### Security

- Documentation now makes clear that only Minibridge owns the public listener;
  its child server remains reachable only through the private stdio pipe.

### Upgrade Notes

- Raw-manifest users should reapply their Kustomize overlay or standalone
  manifest. Helm users already received the default affinity in 1.27.0; this
  release makes the same contract explicit across examples and documentation.

## [1.27.0] - 2026-08-07

### Highlights

- Multi-replica Helm deployments now keep each Streamable HTTP session on the
  pod that owns its in-memory Minibridge state.

### New Features

- The Service exposes Acuvity-compatible `service.sessionAffinity` settings,
  defaulting to `ClientIP` with a 600-second timeout.

### Improvements

- Helm resources and pods now carry the standard
  `app.kubernetes.io/component: mcp-server` label without changing immutable
  Deployment selectors.
- The chart documents when an ingress controller also needs cookie or backend
  affinity because it does not preserve distinct client source addresses.

### Bug Fixes

- Fixes intermittent `Session not found` responses when subsequent requests in
  one MCP session were previously balanced to another replica.

### Breaking Changes

- None. Existing endpoints and clients are unchanged; Service routing becomes
  sticky by default.

### Known Issues

- Affinity does not migrate in-memory sessions when their owning pod restarts or
  is evicted. Clients must reconnect and initialize a new session.

### Security

- Keeping a session on its owning pod avoids exposing its session identifier to
  unrelated replicas. Authentication and Minibridge policy remain the security
  boundary.

### Upgrade Notes

- No action is required. Set `service.sessionAffinity: null` only for a
  single-replica deployment or when an upstream proxy provides equivalent
  stickiness.

## [1.26.0] - 2026-08-06

### Highlights

- Minibridge-enabled deployments now state and test the actual public contract:
  MCP 2025-03-26 Streamable HTTP at `/mcp`, with policy enforcement in the
  same single container and no sidecar or transport adapter.

### New Features

- The Helm chart now passes `mcp.path` to Minibridge as
  `MINIBRIDGE_ENDPOINT_MCP`. Docker Compose, the Kustomize overlay and the
  standalone Kubernetes manifest set the same endpoint explicitly, keeping a
  customised client-facing path consistent across every deployment method.

### Improvements

- Transport documentation now distinguishes Minibridge's public Streamable
  HTTP frontend from its private stdio child protocol and references the same
  one-container convention used by Acuvity's registry images.
- Minibridge examples, Argo CD values and both k3s smoke configurations now set
  `mode: http` explicitly, so their Streamable HTTP intent is reviewable rather
  than merely inherited from a default.
- Helm NOTES report the effective client transport and endpoint after install.
- CI renders and inspects a custom Minibridge MCP endpoint, and the disposable
  Jenkins fixture retries transient update-centre failures before failing the
  all-tools smoke.

### Bug Fixes

- Minibridge-enabled ConfigMaps no longer set `MCP_TRANSPORT=stdio`, which
  incorrectly presented the private child protocol as the client-facing
  transport. Direct-server listener variables render only when Minibridge is
  disabled.
- The Minibridge entrypoint clears direct-server listener variables inherited
  from a shared `.env` file or raw base ConfigMap before spawning the private
  child, preventing stale HTTP listener settings from contradicting the
  command Minibridge actually runs.

### Breaking Changes

- None. The public Minibridge endpoint remains Streamable HTTP on port 8000 at
  `/mcp`; existing clients and values files continue to work.

### Known Issues

- None.

### Security

- Preserves a single exposed MCP listener: Minibridge alone binds the public
  port and evaluates policy, while Jenkins MCP Server communicates over an
  in-container stdio pipe and binds no bypassable backend socket.

### Upgrade Notes

- No action is required. `mcp.transport` configures direct-server deployments;
  when `minibridge.enabled=true`, use `minibridge.mode=http` for client-facing
  Streamable HTTP and `mcp.path` for its endpoint (default `/mcp`).

## [1.25.0] - 2026-08-06

### Highlights

- None.

### New Features

- None.

### Improvements

- The `edge-minibridge` image is now built for `linux/arm64` as well as
  `linux/amd64`, matching the default edge image and both released variants. It
  was amd64-only from when the build compiled minibridge from source under
  emulation; it now downloads the matching release archive per platform, so the
  second architecture costs little.
- With minibridge enabled the ConfigMap sets `MCP_TRANSPORT=stdio`, which is what
  actually runs. It previously advertised `streamable-http` and a listener port
  that nothing bound, because the entrypoint passes `--transport stdio` on the
  command line. The rendered configuration now matches the running process.

### Bug Fixes

- None.

### Breaking Changes

- None.

### Known Issues

- None.

### Security

- Removes a latent conflict: minibridge and the MCP server were configured for
  the same port, and only the entrypoint's `--transport stdio` flag kept the
  server from binding it too. Changing that flag would have produced two
  processes contending for the listener. The transport is now stdio in the
  configuration itself.

### Upgrade Notes

- No action required.

## [1.24.0] - 2026-08-05

### Highlights

- None.

### New Features

- None.

### Improvements

- None.

### Bug Fixes

- None.

### Breaking Changes

- None.

### Known Issues

- None.

### Security

- `jenkins_admin_request` no longer returns Jenkins session or CSRF headers to
  the caller. `Set-Cookie`, `X-Jenkins-Crumb` and related headers were passed
  through verbatim, handing an MCP client a usable Jenkins session and crumb
  from a tool intended to relay a response body. Harmless headers still pass
  through.
- The path accepted by `jenkins_admin_request` is now validated structurally
  with `urlsplit` and rebuilt from the parsed components, instead of by string
  prefix checks. The previous checks missed forms including uppercase schemes
  and leading whitespace, either of which could redirect the request away from
  the configured Jenkins.

### Upgrade Notes

- No action required. A caller that relied on reading `Set-Cookie` from
  `jenkins_admin_request` was reading a credential it should not have had.

## [1.23.0] - 2026-08-05

### Highlights

- None.

### New Features

- None.

### Improvements

- None.

### Bug Fixes

- `scripts/set_version.py` refuses to change the version unless release notes
  for it already exist. Bumping without notes produced a release that failed
  validation and could not be retriggered, because the version had already
  changed and the workflow only fires on a version change.
- `test_current_release_has_complete_professional_notes` asserted a literal
  release date, so it failed on every release regardless of content. It now
  checks the date's format and that it matches the heading for that version.

### Breaking Changes

- None.

### Known Issues

- None.

### Security

- None.

### Upgrade Notes

- No action required.

## [1.22.0] - 2026-08-05

### Highlights

- Documentation now describes only what the repository actually defines. The
  client configuration section previously showed an `mcp_servers` YAML structure
  that no component in this project reads.

### New Features

- None.

### Improvements

- The client section states the transport, the `/mcp` path and an endpoint table
  per deployment method, with `kubectl` commands to read back the Service and
  ingress names rather than assuming them. Configuration key names are left to
  each client's own documentation.
- `minibridge.basicAuth` and `minibridge.policer.http` document their purpose in
  `values.yaml`. `basicAuth` authenticates callers of the MCP endpoint;
  `policer.http` delegates decisions to an external policy service, and its
  token is that service's credential.

### Bug Fixes

- `examples/values/minibridge-hardened.yaml` pointed `minibridge.basicAuth` at
  `jenkins-mcp-secrets`, reusing the Jenkins credentials Secret to hold the
  proxy's shared secret. The example now uses a dedicated Secret so the two
  concerns stay separate.

### Breaking Changes

- None.

### Known Issues

- None.

### Security

- Separating the proxy's shared secret from the Jenkins credentials means a
  consumer of one no longer implicitly gains the other.

### Upgrade Notes

- If you copied `minibridge-hardened.yaml`, create the dedicated Secret before
  upgrading:
  `kubectl -n <namespace> create secret generic jenkins-mcp-proxy-auth --from-literal=BASIC_AUTH_SECRET="$(openssl rand -hex 32)"`,
  or set `minibridge.basicAuth.existingSecret` back to your existing Secret.

## [1.21.0] - 2026-08-04

### Highlights

- Release integrity is now enforced end to end: deployable server, image,
  chart, Compose, Kubernetes, Argo CD, and values changes cannot merge without
  a strictly newer synchronized version.
- Releases follow the canonical `VERSION` automatically after a validated
  version-change pull request merges to `main`.

### New Features

- The release workflow is reusable with explicit version, source commit, and
  changelog commit inputs, enabling reproducible historical release recovery.
- A one-time ordered backfill publishes the missing `1.18.0`, `1.19.0`, and
  `1.20.0` releases from their exact release commits.
- A release-impact classifier distinguishes deployable behavior from
  documentation, tests, integration fixtures, and workflow-only changes, then
  compares full SemVer precedence against the pull request base.

### Improvements

- Release publication is idempotent: reruns detect an existing GitHub Release
  and safely skip its image, chart, smoke-test, and release jobs.
- Historical release smoke tests check out the same source commit used to build
  the application images and Helm chart.
- Release runs are serialized so automatic publication and a manual recovery
  tag cannot race while updating mutable image aliases.
- Images and the Helm OCI chart are published only after every release smoke
  test succeeds; failed smoke tests leave no externally published release
  artifacts.
- CI failures name every release-impacting path and show the base and proposed
  versions with the exact remediation command.
- Python metadata validation selects only wheel and source-distribution files,
  so it remains valid when a packaged Helm chart shares the `dist/` directory.

### Bug Fixes

- Merging a complete application/chart version bump no longer leaves GitHub
  Releases and tags behind at an older version.
- Ordered backfill prevents older releases from overwriting the `latest`,
  major, or minor image aliases after a newer version is published.
- The initial release-bump guard no longer rejects its own chart documentation
  update or remove a version pin required by repository consistency checks.
- Idempotent release reruns now reject a pre-existing tag that points to a
  different source commit instead of silently treating it as the requested
  release.
- New publications must be newer than the current latest GitHub Release, so an
  older recovery tag cannot move `latest` or other mutable aliases backward.
- Running `make helm-package` before `make build` no longer makes Twine parse
  the chart archive as a Python distribution.

### Breaking Changes

- None.

### Known Issues

- None known.

### Security

- Release permissions remain job-scoped, recovered artifacts retain the
  existing provenance and SBOM gates, and tag/source identity is verified
  before publication.

### Upgrade Notes

- Update the application image and Helm chart together to `1.21.0`. No runtime
  configuration migration is required. Release maintainers must run `make
  version VERSION=X.Y.Z` for any deployable behavior change; creating a
  matching tag manually remains a recovery option only.

## [1.20.0] - 2026-08-04

### Highlights

- Release preparation now treats deployable version pins and professional
  release notes as one validated contract.

### New Features

- A changelog tool can prepare a release entry, validate every required
  category, and render the exact curated notes used by GitHub Releases.

### Improvements

- Release versioning has one canonical inventory covering all 22
  application-version pins across 17 files, including image tags, Helm,
  Kustomize, Argo CD, Compose, README install commands, and release examples.
- CI now scans every repository text file for stale deployable version pins, so
  a newly added manifest or document cannot silently remain on an old image or
  chart version.
- Release documentation and the pull request checklist now explain how to write
  highlights, compatibility impact, security notes, known issues, and upgrade
  instructions before changing the application/chart version.

### Bug Fixes

- GitHub Releases no longer derive their primary notes from an uncurated commit
  list; they publish the validated entry from this changelog.
- The Helm test hook now retries transient Service endpoint propagation instead
  of failing a healthy installation after one immediate connection attempt.
  The retry window remains bounded by both the hook and Helm test timeouts.

### Breaking Changes

- None.

### Known Issues

- None known.

### Security

- None.

### Upgrade Notes

- Update the application image and Helm chart together to `1.20.0`. No runtime
  configuration migration is required. Release maintainers must complete every
  changelog category before `make version` can prepare a new release.

## [1.19.0] - 2026-08-04

### Highlights

- Jenkins MCP Server can now run behind a hardened, single-container
  Minibridge policy boundary with destructive tools blocked and the remaining
  tool surface smoke-tested through the proxy.
- Helm, Argo CD, Kubernetes, Docker, Compose, and operator documentation were
  audited together so the published `1.19.0` image and chart remain aligned.

### New Features

- Per-field Jenkins credential Secret references, allowing the username and API
  token to come from independently managed Secrets without supporting unsafe
  inline production values.
- An independent Secret/key reference for encrypted Minibridge TLS private-key
  passphrases, while preserving the same-Secret `passSecretKey` option.
- A hardened `compose.yaml` for either the plain server or the single-container
  Minibridge variant with destructive tools blocked.

### Improvements

- Documentation, Argo CD guidance, release instructions, raw manifest paths,
  Docker examples, and version synchronization were audited and refreshed.
- Secret-backed configuration follows the chart convention consistently across
  Jenkins credentials, Minibridge authentication, HTTP policer credentials,
  CA bundles, and encrypted TLS private-key passphrases.

### Bug Fixes

- Remote HTTP policer settings now use the Minibridge v0.8.0 environment names:
  `MINIBRIDGE_POLICER_HTTP_URL`, `MINIBRIDGE_POLICER_HTTP_BEARER_TOKEN`, and
  `MINIBRIDGE_POLICER_HTTP_CA`.
- Raw Minibridge deployments now mount a writable XDG config directory and use
  `/tmp` for transient MCP configuration, so they work with a read-only root
  filesystem like the Helm deployment.

### Breaking Changes

- None.

### Known Issues

- None known.

### Security

- Production Jenkins credentials remain Secret-only; unsafe inline username and
  token values are not supported by the chart.
- The Compose and Minibridge deployments use a read-only root filesystem,
  dropped Linux capabilities, explicit writable temporary paths, and an
  opt-in policy that refuses destructive tools.

### Upgrade Notes

- Update the application image and Helm chart together to `1.19.0`; the chart's
  `appVersion` is the default image tag.
- Existing single-Secret Jenkins credentials and Minibridge TLS passphrase
  settings remain compatible. Per-field Secret references are optional.

## [1.18.0] - 2026-08-04

### Highlights

- The Jenkins-through-Minibridge policy boundary is now verified across the
  complete MCP tool surface without mistaking downstream Jenkins failures for
  policy rejection.

### New Features

- A Minibridge integration smoke test verifies that destructive tools are
  hidden and refused while every non-destructive tool is advertised and reaches
  Jenkins.

### Improvements

- The probe classifies Jenkins DNS, connection, timeout, TLS, and certificate
  errors as evidence that the request passed through Minibridge policy.

### Bug Fixes

- The Minibridge smoke probe now distinguishes an explicit policy rejection
  from Jenkins DNS, connection, timeout, TLS, and certificate failures. Those
  downstream failures prove the call crossed the policy boundary and no longer
  produce false rejection results.
- The Jenkins-through-Minibridge smoke test exercises the complete tool surface:
  destructive tools must be hidden and refused, while every other tool must be
  advertised and reach Jenkins.

### Breaking Changes

- None.

### Known Issues

- None known.

### Security

- Destructive tools are required to remain undiscoverable and explicitly
  refused by Minibridge; all other tools must cross the policy boundary.

### Upgrade Notes

- Update the application image and Helm chart together to `1.18.0`. No runtime
  configuration migration is required.

## [1.17.0] - 2026-08-03

### Changed

The chart no longer assumes a Tailscale deployment. Defaults now describe a
plain Kubernetes cluster, and every environment-specific value is opt-in.

- `ingress.enabled` defaults to `false`. `ingress.className` and
  `ingress.annotations` are empty rather than hardcoded to `tailscale`; an empty
  class uses the cluster's default IngressClass. Annotation examples for nginx,
  cert-manager and the Tailscale operator are in `values.yaml`.
- `tailscale.enabled` is a new master switch, default `false`. No Tailscale
  resource renders unless it is true, and enabling a sub-feature while it is
  false fails the render rather than producing nothing.
- `jenkins.url` has no default and is now required, with a clear message when
  it is missing. `tailscale.egress.tailnetFQDN` likewise has no default and is
  required when the egress proxy is enabled.

`jenkins.verifyTls` remains `true`. Defaulting certificate verification off
would silently accept any certificate on a connection carrying a Jenkins API
token. Where the issuer is private, set `jenkins.caBundle.existingSecret`, which
stays optional and empty by default.

### Migration

A release relying on the previous Tailscale defaults must now set them
explicitly:

```yaml
jenkins:
  url: https://jenkins.your-tailnet.ts.net
ingress:
  enabled: true
  className: tailscale
  hostname: jenkins-mcp
  annotations:
    tailscale.com/proxy-group: jenkins-mcp-ingress
tailscale:
  enabled: true
  egress:
    enabled: true
    tailnetFQDN: jenkins.your-tailnet.ts.net
```

`examples/values/tailscale-production.yaml` shows the full configuration.

## [1.16.0] - 2026-08-03

### Fixed

- **Every pod crash-looped on default values.** `MCP_MAX_LOG_BYTES` rendered as
  `"1e+06"`: Helm formats a large number in scientific notation unless it is
  cast, and the server rejects that at startup. The manifests rendered fine and
  `helm lint` passed, so nothing caught it until the chart was installed into a
  real cluster. Numeric values now pass through `int` before quoting, and a test
  asserts both that the template casts and that the server really does reject
  scientific notation.
- The chart's own `helm test` pod targeted `mcp.healthPort` and `/readyz`
  directly. With minibridge enabled the health port moves and the path becomes
  `/`, so `helm test` would have failed against a healthy deployment. It now
  uses the same helpers as the Service and NetworkPolicy, and is omitted when
  `service.exposeHealthPort` is false.

### Added

- **The chart is now installed into a real k3s cluster in CI.** `helm lint` and
  `helm template` only prove the manifests render; the smoke test proves the API
  server accepts them, the pod starts, the probes pass, the chart's own test pod
  reaches the Service through the NetworkPolicy, an upgrade over an existing
  release is clean, and uninstall leaves nothing behind. It runs across
  Kubernetes 1.33 to 1.36, on every change and again as a release gate, so a
  chart that cannot install is never published. Defined once as a reusable
  workflow and called from both.
- A Kubernetes compatibility matrix in the chart README, listing the minors that
  are actually installed and tested, and the apiVersion of every resource the
  chart emits.

## [1.15.0] - 2026-08-03

### Added

- **The `-minibridge` image is now built and published.** The chart selected a
  tag nothing produced, so any minibridge deployment would have failed with
  ImagePullBackOff. Release publishes it multi-arch alongside the default image,
  publish-edge builds `edge-minibridge`, and CI builds it on every pull request
  so a broken `Dockerfile.minibridge` fails review rather than release.
- `integration/jenkins/plugins-legacy.txt` and an `init.groovy.d` bootstrap,
  which make an older controller testable: pinned plugin versions with
  `--latest false` for dependencies, and no dependency on Configuration as Code,
  which is not installed everywhere.

### Changed

- **Jenkins 2.504.1 is now verified.** All 23 tools pass the full integration
  suite against it, using the pinned plugin set. Four LTS lines are now verified:
  2.555.x, 2.541.3, 2.504.3 and 2.504.1.
- minibridge is pinned to a specific commit rather than `latest`. A proxy in the
  request path should not change enforcement behaviour on an unrelated rebuild.


## [1.14.0] - 2026-08-03

### Fixed

- **Documented install commands pinned an eleven-release-old chart.** Both the
  root and chart READMEs told people to run `helm upgrade --install --version
  1.2.0`. Several other files added after `scripts/set_version.py` were never
  wired into it and had frozen too: the minibridge kustomize overlay and
  standalone deployment at 1.8.0, and the generic Argo CD application at 1.12.0.
- `scripts/check_version.py` now scans `README.md`, `deploy/`, `examples/` and
  `charts/` for any version pin that disagrees with `VERSION`, so a file added
  without wiring it into the release script fails the build instead of silently
  going stale.

### Changed

- The chart README documented 7 of 32 top-level values, omitting `minibridge`
  and `autoscaling` entirely. Replaced with a reference covering all of them,
  grouped by connection, credentials, server policy, minibridge, workload,
  networking and audit, plus a table of the example values files. A test fails
  if any value goes undocumented.
- Refreshed the illustrative versions in the release documentation, which still
  referenced 1.2.1.

## [1.13.1] - 2026-08-03

### Changed

- All 23 tools are now verified against **Jenkins 2.504.3**, the latest patch of
  the 2.504 LTS line, alongside 2.555.x and 2.541.3. The matrix records three
  verified LTS lines.
- Removed `2.50` from the matrix and docs. It is not a published Docker tag, so
  presenting it as a testable version was misleading.
- Documented why 2.504.1 cannot be built from scratch: current plugins require
  2.504.3, and the per-line update centres that once served 2.504.1-era plugins
  have been retired and return 404. That is a constraint on assembling a test
  image, not a property of this server; a running 2.504.1 controller is
  unaffected.

### Fixed

- The 1.13.0 entry appeared twice with different content. Merged into one.

## [1.13.0] - 2026-08-03

### Fixed

- Triggering a parameterised job with an explicitly empty parameter object used
  `/build` instead of `/buildWithParameters`, which Jenkins can reject on a
  parameterised job. The endpoint is now chosen on `parameters is not None`, so
  an empty object means "use the defaults".

### Added

- A compatibility matrix in the README naming the concrete cores that were
  tested, with the result of each, and an explicit statement that unverified
  rows are not the same as incompatible.
- Documented plugin, permission, proxy, job-configuration and scale blockers in
  `docs/JENKINS_COMPATIBILITY.md`, derived from what the client actually does.
  The two most likely to affect a large installation: Strict Crumb Issuer with
  client-IP checking breaks every write behind SNAT or an egress proxy, and
  `Job/Read` without `Job/ExtendedRead` breaks `get_job_config` alone.
- Verified that a Jenkins served under a path prefix, such as
  `https://ci.corp/jenkins`, keeps that prefix when the client builds URLs.

### Fixed

- On a controller with CSRF protection disabled, the crumb issuer was probed
  before **every** write and 404'd every time. The result is now cached, so the
  probe runs once per client.

### Added

- Per-tool plugin requirements in `docs/JENKINS_COMPATIBILITY.md`: which plugin
  each tool needs and what fails without it. Most of the surface is core-only,
  and a missing plugin fails that tool alone rather than the server.
- A "things that actually break it" section covering reverse proxies stripping
  `Authorization` or `Jenkins-Crumb`, insufficient Jenkins permissions, Script
  Security approval, and plugin versions that require a newer core.
- `integration/jenkins/Dockerfile.legacy`, which resolves plugins from the
  controller's own update centre line so an older core can be tested. Selected
  with `JENKINS_DOCKERFILE`; the default suite is unchanged.

### Verified

- A Jenkins served under a context path (`https://ci.example.com/jenkins`)
  works; the client merges the base path correctly.
- A controller with CSRF protection disabled works; writes proceed with no crumb
  header.
- The generated job XML references plugins without version pins, so this server
  imposes no plugin version floor.

## [1.12.0] - 2026-08-03

### Added

- `docs/JENKINS_COMPATIBILITY.md`: which Jenkins versions are supported, the
  complete list of REST endpoints the server calls, which plugins each tool
  needs, and the least Jenkins permissions for each tool. CI verifies against
  `jenkins/jenkins:lts-jdk21`, currently the 2.555.x LTS line. A test asserts
  the documented endpoint list does not drift from `client.py`.
- `service.exposeHealthPort`, default true. `/readyz` reports configuration
  state, so it can now be kept off a Service that is reachable from outside the
  cluster.
- `examples/argocd/application-hpa-generic.yaml`: a plain-Kubernetes Argo CD
  Application with an nginx ingress, cert-manager, Tailscale off and autoscaling
  on, including the `ignoreDifferences` for `/spec/replicas` that stops Argo CD
  fighting the HPA.
- `examples/argocd/application-oci.yaml` now also covers autoscaling, the
  PodDisruptionBudget, resources, audit storage and the NetworkPolicy, each with
  the reasoning inline.

## [1.11.0] - 2026-08-03

### Fixed

- **The NetworkPolicy blocked the health port whenever minibridge was enabled.**
  Both `networkpolicy.yaml` and `service.yaml` used `mcp.healthPort` directly
  instead of the effective port, which minibridge moves from 8081 to 8080. The
  Service still worked through its named target port, but the NetworkPolicy
  allowed a port nothing was listening on.
- `audit.storage.type: pvc` produced a raw Go nil-pointer error rather than the
  intended message, because `audit.storage.persistentVolumeClaim` did not exist
  in values, so `.claimName` dereferenced nil before `required` could fire.
- The PodDisruptionBudget guard only considered `autoscaling.minReplicas`. With
  autoscaling off, `replicaCount: 1` and the default `minAvailable: 1` left no
  evictable pod, so a node drain would block indefinitely. The guard now uses
  whichever minimum applies.
- `NOTES.txt` assumed Tailscale and reported the wrong endpoint for any other
  ingress class, said nothing about minibridge, and did not reflect whether the
  policer was enforcing or only logging.

### Changed

- The values schema now covers all 17 previously unvalidated top-level values
  and sets `additionalProperties: false`, so a typo such as `replicaCoun` fails
  the render instead of being silently ignored. `global` is permitted so the
  chart still works as a subchart.
- `NOTES.txt` prints the effective endpoint for the configured ingress class,
  the minibridge policy in force, the server policy that applies regardless, and
  a warning when `jenkins.verifyTls` is false.

## [1.10.0] - 2026-08-03

### Fixed

Settings that contradicted each other, or were silently ignored, now fail
loudly instead of resolving in a way nobody asked for.

- **A CA bundle with `verifyTls: false` silently re-enabled verification.**
  `Settings.verify` returns the bundle path whenever one is set, ignoring
  `verifyTls` entirely, so anyone who disabled verification and also supplied a
  bundle got verification back on. Either behaviour would be a guess about
  intent, so the combination is now rejected at startup and at render time.
- `jenkins.caBundlePath` and `jenkins.caBundle.existingSecret` set together
  silently mounted the Secret and then ignored it, because the path wins.
- **minibridge settings did nothing while `minibridge.enabled` was false.** Tool
  policy, guardrails, basic auth and TLS could all be configured and silently
  enforced nothing, which is the worst way for a security control to fail. The
  render now fails and names every setting that would be ignored.
- `ingress.tlsSecretName` with `ingress.tls: false` silently dropped the secret.

### Changed

- `jenkins.verifyTls` and the two CA settings are documented in `values.yaml`.
  The CA bundle is optional and unnecessary for a publicly issued certificate,
  which includes Let's Encrypt, any commercial CA, and Tailscale. It is needed
  only for a private CA or a self-signed certificate. The docs also spell out
  why `verifyTls: false` is the wrong fix for a certificate error.

## [1.9.0] - 2026-08-03

### Added

- `HorizontalPodAutoscaler` via `autoscaling.*`, with CPU and memory targets,
  raw `extraMetrics`, and `behavior`. The Deployment stops declaring `replicas`
  while it is enabled, so the HPA owns the count instead of Helm or Argo CD
  reverting it on every sync.
- `revisionHistoryLimit` is now a value rather than hardcoded.
- `ingress.tlsSecretName`, `ingress.hostRule` and `ingress.extraRules`.
- `examples/values/generic-ingress-hpa.yaml`: a plain Kubernetes deployment with
  an nginx ingress, cert-manager TLS, Tailscale disabled and the HPA on.

### Fixed

- **The Ingress was unusable outside Tailscale.** It emitted no `rules[].host`
  and no TLS `secretName`. The Tailscale Operator wants exactly that, but on a
  general controller a rule without a host matches every hostname reaching it.
  The template now picks the shape from `ingress.className`, overridable with
  `ingress.hostRule`.
- **Duplicate `revisionHistoryLimit` key in the Deployment.** Two keys were
  emitted and YAML silently kept the last, so the value was 5 regardless.
  `helm lint` does not detect this. The default stays 5, preserving behaviour.
- **Two credential combinations resolved silently.** Enabling `externalSecret`
  while `jenkins.credentials.existingSecret` was still set (the default) made
  the chart read from the ExternalSecret target and ignore `existingSecret`
  without a word; `credentials.create` alongside `existingSecret` did the same.
  All three pairings now fail the render with an explanation of which to clear.
- The credential guard moved into `_validate.tpl` and is included from the
  secret templates, so the mutual-exclusion message surfaces instead of an
  unrelated `required` error from `secret.yaml`.
- A `PodDisruptionBudget` whose `minAvailable` is at or above
  `autoscaling.minReplicas` now fails the render. That combination lets no pod
  be evicted at minimum scale, which blocks node drains.

## [1.8.0] - 2026-08-02

### Changed

- **Migrated to the MCP Python SDK 2.x.** `mcp.server.fastmcp.FastMCP` was removed
  in mcp 2.0 and replaced by `mcp.server.mcpserver.MCPServer`, which has the same
  surface (`.tool()`, `.run()`, `.streamable_http_app()`). Two call sites moved:
  - `stateless_http` is now an argument to the transport call rather than to the
    constructor.
  - The listener options (`host`, `port`, `streamable_http_path`) are transport
    arguments instead of mutable attributes on `mcp.settings`.
- `MCPServer` accepts `version` directly, so the private `mcp._mcp_server.version`
  assignment added in 1.3.0 is gone. `initialize` still reports the application
  version in `serverInfo`, now through a supported API.
- The integration workflow's standalone `mcp` pin moved to 2.x alongside the
  package.

### Removed

- The Dependabot ignore rule holding `mcp` on 1.x. It was never effective — the
  `update-types: version-update:semver-major` filter is not honoured for pip range
  requirements, so Dependabot re-proposed the major bump six hours after the rule
  landed. With the migration done the rule is unnecessary.

### Verified

Migration checked end to end against mcp 2.0.0: mypy, ruff and the full suite
pass, and a wheel built from the migrated source serves `/healthz` and `/readyz`,
completes an `initialize` handshake reporting version 1.8.0, and registers all 23
tools.

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
