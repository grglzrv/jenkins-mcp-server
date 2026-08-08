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
| `GET /api/json` | `list_jobs`, `list_nodes`, readiness |
| `GET /crumbIssuer/api/json` | CSRF crumb for every write |
| `GET /job/…/api/json` | `get_job`, `get_build_info`, `get_queue` |
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
| Thousands of jobs | `list_jobs` returns what Jenkins returns. Use the folder argument to scope it rather than listing the root |
| Very large console logs | Capped by `MCP_MAX_LOG_BYTES`, default 1 MB, and paginated. The response reports `truncated` and the offset to resume from |
| Busy queue | `trigger_build` returns as soon as the item is queued. The build number does not exist until it leaves the queue, so poll `get_build_info` before addressing a build by number |
| Multiple MCP replicas | Each maintains its own crumb and session. No shared state, so replicas do not interfere |

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

The server fetches a CSRF crumb before every write and reissues it once if
Jenkins rotates the session, so a crumb expiring mid-session does not surface as
a 403.

## Permissions

Grant the service account the least that covers the tools you enable:

| Tools | Jenkins permission |
| --- | --- |
| All read tools | `Overall/Read`, `Job/Read` |
| `trigger_build`, `scan_multibranch_pipeline` | `Job/Build` |
| `stop_build`, `cancel_queue_item` | `Job/Cancel` |
| `create_*`, `copy_job`, `update_job_config` | `Job/Create`, `Job/Configure` |
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
| `Job/Build` | `Job/Cancel` | `stop_build` and `cancel_queue_item` fail |
| Job permissions | `Agent/Disconnect` | `set_node_offline` fails |

## Java

Jenkins 2.555.x requires Java 21. This is a property of your Jenkins
installation and has no bearing on the MCP server, which is Python and runs in
its own container.
