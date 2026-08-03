# Jenkins compatibility

## Measured results

`.github/workflows/compatibility.yml` runs the full integration suite against
several Jenkins cores and records the outcome. Run it on demand from the Actions
tab, optionally with `extra_image` to test a specific tag. It also runs monthly,
to catch the `lts` tag moving.

| Jenkins core | Result | Notes |
| --- | --- | --- |
| `jenkins/jenkins:lts-jdk21` (2.555.x) | **pass** | The version CI uses for the normal suite |
| `jenkins/jenkins:2.541.3-jdk21` | **pass** | |
| `jenkins/jenkins:2.504.3-jdk21` | **pass** | Latest patch of the 2.504 LTS line |
| `jenkins/jenkins:2.504.1-jdk21` | **pass** | With `plugins-legacy.txt` pinned to the versions that controller runs |
| `jenkins/jenkins:2.492.3-jdk21`, `2.462.3-jdk21` | not buildable | Same |
| `jenkins/jenkins:2.401.3`, `2.319.3` | not buildable | Same |

"Not buildable" is a statement about assembling a test image in August 2026,
not about this server. Those runs fail before the MCP server starts: current
plugins declare a minimum core newer than the one under test, and the per-line
update centres that once served matching plugin versions have been retired and
return 404. A controller already running one of those versions is unaffected,
because it holds plugin versions installed when they were current.

What this establishes: **all 23 tools are verified against three LTS lines,
2.555.x, 2.541.3 and 2.504.3.** For a core on the same line as one of those,
the difference is backported fixes rather than API changes. For an older line,
pin the plugin versions your controller actually runs and run the suite against
it — that tests the combination you operate, which is worth more than any
generic matrix.

## Supported versions

| | Version | Notes |
| --- | --- | --- |
| **Verified in CI** | `jenkins/jenkins:lts-jdk21` | The integration suite builds this image on every change to `src/`, and exercises job creation, triggering, console streaming, stopping and deletion end to end. At the time of writing that tag resolves to the **2.555.x** LTS line. |
| **Recommended** | Current LTS line | Only the most recent LTS line receives security backports. |
| **Unverified** | Older Jenkins 2.x | Every endpoint this server calls has been part of core since early 2.x, so an older core will most likely work, but none has been demonstrated. See the measured results above. |
| **Not supported** | Jenkins 1.x | Different URL scheme and no folder support. |

The honest position on older releases: the REST endpoints used here are old and
stable, so a much older 2.x will probably work. It is not tested, and anything
outside the current LTS line is unpatched, so it is not recommended. If you are
on something older and it works, that is fortunate rather than guaranteed.

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

## Things that actually break it

Ordered by how often they bite in practice.

**A reverse proxy stripping headers.** The server sends `Authorization` for auth
and `Jenkins-Crumb` for CSRF. A proxy that drops either produces 401 or 403 on
every write while reads keep working, which is a confusing signature. Check the
proxy forwards both before suspecting the server.

**Insufficient Jenkins permissions.** The commonest cause of a tool failing while
its neighbours succeed. See the permission table below. Jenkins permissions are
the outer boundary: no MCP setting can grant what Jenkins denies.

**Plugin versions requiring a newer core.** Not a runtime problem, but it blocks
building a test environment: current `workflow-multibranch`, `branch-api` and
`git-client` require Jenkins **2.504.3**, so a 2.504.1 controller cannot install
them. If your controller already runs these plugins, this does not affect you.

**Script Security approval.** `create_pipeline_job` writes `<sandbox>true</sandbox>`.
On a controller with strict script approval, the created job may need an
administrator to approve the script before it runs. The job is created either
way; the build is what stalls.

**Folder-scoped or non-Jenkins job types.** Anything created outside
`cloudbees-folder` semantics, and job types from plugins this server does not
model, are still readable but cannot be created through the typed tools. Use
`create_job_from_xml` with your own `config.xml` for those.

**Not a problem, verified:** a Jenkins served under a context path such as
`https://ci.example.com/jenkins` works. The client merges the base path
correctly, so `JENKINS_URL` may include one. A controller with CSRF protection
disabled also works: the crumb issuer is probed once, and writes proceed without
a crumb header.

## Plugins and configurations that break tools

Grouped by what actually goes wrong. Every entry was derived from what the
client code does, not from guesswork about Jenkins in general.

### Missing plugins

| Missing | Breaks | Symptom |
| --- | --- | --- |
| `cloudbees-folder` | Any nested job path such as `AI/nightly` | 404 on every folder path; only top-level jobs resolve |
| `workflow-aggregator` (Pipeline) | `create_pipeline_job`; `term` and `kill` modes of `stop_build` | Job type not recognised on create; `stop` still works |
| `workflow-multibranch` + `branch-api` | `create_multibranch_pipeline`, `scan_multibranch_pipeline` | Job type not recognised |
| `git` / `git-client` | Multibranch jobs with a Git source | Create succeeds, scanning finds nothing |

Everything else keeps working. A missing plugin fails one tool, not the server.

### Permissions that look like bugs

| Configuration | Breaks | Why |
| --- | --- | --- |
| Account has `Job/Read` but not `Job/ExtendedRead` or `Job/Configure` | `get_job_config` | Reading `config.xml` needs extended read; plain read is not enough |
| Folder-level authorisation without `Job/Create` on the target folder | `create_*`, `copy_job` | Permission is evaluated on the parent folder, not the root |
| `Job/Build` without `Job/Cancel` | `stop_build`, `cancel_queue_item` | Triggering and cancelling are separate permissions |
| Agent permissions not granted | `set_node_offline` | Needs `Agent/Disconnect` |

### CSRF and proxies

| Configuration | Effect |
| --- | --- |
| CSRF protection disabled | Handled. The client detects the 404 from `/crumbIssuer`, caches that fact, and stops asking |
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

## Java

Jenkins 2.555.x requires Java 21. This is a property of your Jenkins
installation and has no bearing on the MCP server, which is Python and runs in
its own container.
