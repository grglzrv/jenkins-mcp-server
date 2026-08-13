# Jenkins compatibility

## Supported versions

| Jenkins core | Status |
| --- | --- |
| `2.555.x` (`lts-jdk21`) | Verified. Runs on every change to `src/` |
| `2.541.3` | Verified |
| `2.504.3` | Verified |
| `2.504.1` | Verified, using `integration/jenkins/plugins-legacy.txt` |
| Other 2.x | Supported. Every endpoint used has been stable in core since early 2.x, but these are not covered by CI |
| 1.x | Not supported. Different URL scheme, no folders |

Prefer the current LTS line: it is the only one receiving security backports.

`.github/workflows/compatibility.yml` produces these results. Run it from the
Actions tab, optionally with `extra_image` to test a specific tag; it also runs
monthly to catch the `lts` tag moving.

### Testing a core older than the current plugin set

Plugins declare a minimum core version, and current releases of
`workflow-multibranch`, `branch-api` and the `workflow-*` set require a newer
core than several supported Jenkins versions, so installing them on an older
controller fails.

Pin the plugin versions your controller runs instead.
`integration/jenkins/Dockerfile.legacy` resolves dependencies with
`--latest false` so they settle on the minimum each pinned plugin requires, and
bootstraps through `init.groovy.d` rather than Configuration as Code, which is
not installed on every controller.

## Verifying against your own controller

```bash
JENKINS_URL=https://jenkins.example.com \
JENKINS_USERNAME='<actual-jenkins-login-id>' \
JENKINS_TOKEN=<api-token> \
  ./scripts/dump_jenkins_plugins.sh > integration/jenkins/plugins-legacy.txt

JENKINS_IMAGE=jenkins/jenkins:<your-version>-jdk21 \
JENKINS_DOCKERFILE=Dockerfile.legacy \
  ./integration/run.sh
```

`dump_jenkins_plugins.sh` reads `/pluginManager/api/json`, writes `name:version`
lines, skips disabled plugins, and reports on stderr any plugin the suite needs
that is not installed. Pass `--all` for every plugin.

## What the server actually calls

Nothing exotic. This is the complete surface:

| Endpoint | Used by |
| --- | --- |
| `GET /api/json` | `list_jobs` |
| `GET /crumbIssuer/api/json` | Initial CSRF crumb plus stale-crumb and transient-404 recovery |
| `GET /job/…/api/json` | `get_job`, `get_build_info` |
| `GET /queue/api/json` | `get_queue` |
| `GET /job/…/config.xml` | `get_job_config` |
| `POST /job/…/config.xml` | `update_job_config` |
| `POST /createItem` | `create_job_from_xml`, `create_pipeline_job`, `create_multibranch_pipeline`, `copy_job` |
| `POST /job/…/doDelete` | `delete_job` |
| `POST /job/…/build`, `/buildWithParameters` | `trigger_build`, `scan_multibranch_pipeline` |
| `POST /job/…/enable`, `/disable` | `enable_job`, `disable_job` |
| `GET /job/…/<n>/logText/progressiveText` | `get_build_console` |
| `POST /job/…/<n>/stop`, `/term`, `/kill` | `stop_build` |
| `POST /queue/cancelItem` | `cancel_queue_item` |
| `GET /computer/api/json`, `POST /computer/<n>/toggleOffline` | `list_nodes`, `get_node`, `set_node_offline` |

`list_nodes` and `get_node` deliberately request and return only node status.
Executor and `currentExecutable` fields are excluded; use
`list_running_builds` for allowlist-filtered running-job visibility.

`get_queue` and `list_running_builds` also use explicit Jenkins `tree` queries
and response projections. Queue action/build-parameter payloads and arbitrary
plugin fields are not part of these tools; `get_build_info` remains the tool for
documented build parameters after Jenkins assigns a build number.

`/term` and `/kill` are Pipeline-only. On a freestyle job only `stop` applies;
the other two return an error from Jenkins, which surfaces as a tool error
rather than a silent no-op.

## Plugin requirements per tool

Most of the surface is core-only. Only the Pipeline and multibranch tools depend
on plugins, and a missing plugin fails **that tool alone** with a Jenkins error;
the rest keep working and the server stays up.

| Tool | Needs | If missing |
| --- | --- | --- |
| `list_jobs`, `get_job`, `get_job_config`, `get_build_info`, `get_build_console`, `list_running_builds`, `get_queue`, `list_nodes`, `get_node` | core | — |
| `create_job_from_xml`, `copy_job`, `update_job_config`, `delete_job`, `enable_job`, `disable_job`, `trigger_build`, `cancel_queue_item`, `set_node_offline`, `jenkins_admin_request` | core | — |
| **Any tool given a job path containing `/`** | `cloudbees-folder` | The path does not resolve. Only top-level jobs work. This affects every tool, so treat it as required. |
| `create_pipeline_job` | `workflow-job`, `workflow-cps` | `createItem` rejects the `CpsFlowDefinition` class |
| `stop_build` with `term` or `kill` | `workflow-job` | Only `stop` works; `term`/`kill` return an error. Freestyle jobs never support them. |
| `create_multibranch_pipeline`, `scan_multibranch_pipeline` | `workflow-multibranch`, `branch-api`, `git` | `createItem` rejects the multibranch classes |

The job XML this server generates references plugins **without version pins**
(`plugin="workflow-job"`, not `plugin="workflow-job@1571.v..."`), so Jenkins
accepts whatever version is installed. There is no plugin version floor imposed
by this server.

### Multibranch input contract

`create_multibranch_pipeline` accepts ordinary HTTPS URLs, SSH URLs with a
username such as `ssh://git@host/org/repo.git`, and SCP-style Git remotes such as
`git@host:org/repo.git`. Authentication belongs in a Jenkins credential named by
`credentials_id`; URL passwords, non-SSH userinfo, query strings, and fragments
are rejected rather than persisted in `config.xml`.

`script_path` is a repository-relative SCM path. Use `Jenkinsfile` or a canonical
forward-slash path such as `ci/Jenkinsfile`. Absolute paths, Windows drive paths,
backslashes, repeated separators, and `.` or `..` segments fail locally before a
crumb or create request reaches Jenkins.

## Verified as non-issues

| Configuration | Behaviour |
| --- | --- |
| Jenkins under a context path, e.g. `https://ci.example.com/jenkins` | Works. Put the full prefix in `JENKINS_URL` |
| CSRF protection disabled | Works. The crumb issuer is probed once and writes proceed without a crumb |

## Configurations that break tools

### CSRF and proxies

| Configuration | Effect |
| --- | --- |
| Standard crumb issuer | Works. The crumb is fetched once, reused, and reissued once automatically if Jenkins rotates the session |
| **Strict Crumb Issuer** with *check client IP* enabled | **Breaks writes.** The crumb is bound to the source IP. Behind SNAT, an egress proxy, or with several replicas, the write can arrive from a different address than the crumb request. Disable the IP check, or exclude the MCP server's identity |
| Reverse proxy stripping unknown request headers | **Breaks writes.** The crumb travels in a header named by Jenkins, commonly `Jenkins-Crumb`. It must reach Jenkins intact |
| Jenkins under a path prefix, e.g. `https://ci.corp/jenkins` | Works. Verified: the prefix is preserved when the client builds URLs. Put the full prefix in `JENKINS_URL` |

### Job configuration

| Situation | Effect |
| --- | --- |
| Parameterised job | Pass `parameters`, even an empty object, so the trigger uses `buildWithParameters`. Triggering a parameterised job with no parameters at all uses `/build`, which Jenkins may reject |
| Pipeline script needing script approval | `create_pipeline_job` succeeds, the build then blocks pending admin approval. Sandbox-safe scripts are unaffected |
| Job names containing `/` | Treated as folder separators, which is the intended behaviour. Names containing `.` are fine; `.` and `..` as whole path segments are rejected as traversal |
| Jenkins managed by Configuration as Code with jobs read-only | `create_*`, `update_job_config` and `delete_job` are refused by Jenkins |
| Agents provisioned by a cloud plugin (Kubernetes, EC2) | `set_node_offline` on an ephemeral agent may 404 or have no lasting effect, since the agent disappears |

### Scale considerations for a large controller

| Situation | Effect |
| --- | --- |
| Thousands of jobs | `list_jobs` filters Jenkins results through `MCP_ALLOWED_JOBS`; use the folder argument to avoid fetching a large root listing before filtering |
| Very large API or job-config response | Every complete response is streamed up to `MCP_MAX_RESPONSE_BYTES`, default 10 MB, then refused rather than returned partially. Narrow folder queries first; raise the bound only for a measured legitimate response |
| Very large job definition, administrator body, or build parameters | The exact encoded body is refused above `MCP_MAX_REQUEST_BYTES`, default 10 MB, before a crumb or Jenkins request. Reduce it or raise the bound only for a measured legitimate request |
| Very large console logs | Streamed only up to `MCP_MAX_LOG_BYTES`, default 1 MB, and paginated. The response reports `truncated` and the offset to resume from without buffering the full Jenkins response |
| Malformed progressive-log offset | A non-integer, negative, or forward-pagination offset behind the delivered bytes is rejected as an invalid Jenkins response instead of causing a raw exception or pagination loop |
| Busy queue | `trigger_build` returns as soon as the item is queued. The build number does not exist until it leaves the queue, so poll `get_build_info` before addressing a build by number |
| Multiple MCP replicas | Each maintains its own crumb and session. No shared state, so replicas do not interfere |

Safe reads retry 429 and transient gateway/network failures, respecting a
bounded `Retry-After` value or using jittered backoff. Writes retry only
connection establishment, connection timeout, and connection-pool acquisition
failures, where the request body was not sent. They are not replayed after
429/502/503/504 responses, read timeouts, write timeouts, or ambiguous protocol
errors.

Each server replica also limits Jenkins traffic to `JENKINS_MAX_CONCURRENCY`
(Helm `jenkins.maxConcurrency`, default 10) requests in flight. Excess requests
wait at most `JENKINS_TIMEOUT_SECONDS` for a local slot before failing as a pool
timeout; that failure is safe to retry because no request reached Jenkins. The
limit is per replica, so the deployment-wide ceiling is the configured value
multiplied by the number of replicas.

## Authentication

Username plus **API token**, sent as HTTP basic auth. Use a token, not the
account password: tokens are revocable individually and are not accepted for
interactive login.

For an LDAP security realm, `JENKINS_USERNAME` is the actual value that replaces
`{0}` in the controller's configured **User search filter**. With the common
`uid={0}` filter, use the account's LDAP `uid`; with a custom filter, use the
matching attribute value. There is no universal object-ID format. Do not copy a
documentation placeholder or use a display name/email unless the filter
searches that field. Generate `JENKINS_TOKEN` from that same Jenkins user.

Create one at *People → your user → Security → API Token → Add new Token*.

The server fetches a CSRF crumb before the first write and reuses it. A 404 is
negative-cached for controllers with CSRF disabled. If Jenkins later rejects a
write as missing a crumb, the client treats that rejection as proof that the
404 was transient, clears the negative cache, and performs one single-flight
re-probe. A stale crumb is likewise reissued once, so rotation does not surface
as a permanent 403.

## Permissions

Grant the service account the least that covers the tools you enable:

| Tools | Jenkins permission |
| --- | --- |
| All read tools except `get_job_config` | `Overall/Read`, `Job/Read` |
| `get_job_config` | `Job/ExtendedRead` |
| `trigger_build`, `scan_multibranch_pipeline` | `Job/Build` |
| `stop_build`, `cancel_queue_item` | `Job/Cancel` |
| `create_job_from_xml`, `create_pipeline_job`, `create_multibranch_pipeline` | `Job/Create` on the target parent |
| `copy_job` | `Job/ExtendedRead` on the source and `Job/Create` on the target parent. Jenkins also requires `Job/Configure` on the source when its configuration contains secrets hidden from extended-read users |
| `update_job_config` | `Job/Configure` |
| `delete_job` | `Job/Delete` |
| `enable_job`, `disable_job` | `Job/Configure` |
| `set_node_offline` | `Agent/Disconnect` |
| `jenkins_admin_request` | Depends entirely on the path called |

Jenkins permissions are the outer boundary. The server's `MCP_ALLOW_*` settings
and the optional minibridge tool policy narrow things further, but they cannot
grant anything Jenkins itself denies. Restricting the Jenkins account is the
control that still holds if the server is misconfigured.

Common mistakes, where one tool fails while its neighbours succeed:

| Granted | Missing | Result |
| --- | --- | --- |
| `Job/Read` | `Job/ExtendedRead` | `get_job_config` fails; reading `config.xml` needs extended read |
| `Job/Create` at the root | `Job/Create` on the target folder | `create_*` and `copy_job` fail; permission is evaluated on the parent folder |
| `Job/Create` on the target | `Job/ExtendedRead` on the source, or `Job/Configure` when its config contains redacted secrets | `copy_job` fails while direct creation still works |
| `Job/Build` | `Job/Cancel` | `stop_build` and `cancel_queue_item` fail |
| Job permissions | `Agent/Disconnect` | `set_node_offline` fails |

## Java

Jenkins 2.555.x requires Java 21. This is a property of your Jenkins
installation and has no bearing on the MCP server, which is Python and runs in
its own container.
