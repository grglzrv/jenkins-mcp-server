# Jenkins compatibility

## Measured results

`.github/workflows/compatibility.yml` runs the full integration suite against
several Jenkins cores and records the outcome. Run it on demand from the Actions
tab, optionally with `extra_image` to test a specific tag. It also runs monthly,
to catch the `lts` tag moving.

| Jenkins core | Result | Notes |
| --- | --- | --- |
| `jenkins/jenkins:lts-jdk21` (2.555.x) | **pass** | The version CI uses for the normal suite |
| `jenkins/jenkins:2.541.3-jdk21` | **pass** | Previous LTS line |
| `jenkins/jenkins:2.401.3` | not established | The test image cannot be built: no plugin installer on `PATH` |
| `jenkins/jenkins:2.319.3` | not established | Same |
| `jenkins/jenkins:2.263.4` | not established | Base image `apt-get` fails; the Debian release it used is archived |
| `jenkins/jenkins:2.222.4` | not established | Same, `Failed to fetch deb.debian.org` |
| `jenkins/jenkins:2.50` | **cannot be tested** | No such published tag on Docker Hub |

Read those "not established" rows carefully: they are failures of the *test
harness*, not evidence that the server is incompatible. Older images cannot be
provisioned with current plugins, and their Debian bases no longer resolve. The
server was never reached, so nothing was learned about it either way.

What this does establish: **the two most recent LTS lines are verified working,
and nothing older has been demonstrated.** If you need an older core, test it
against your own environment rather than trusting the endpoint list.

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

## Required plugins

Core-only Jenkins covers the freestyle and node tools. These plugins are needed
for the rest, and are what the integration suite installs:

| Plugin | Needed for |
| --- | --- |
| `cloudbees-folder` | Folder paths such as `AI/nightly`. Without it, only top-level jobs resolve. |
| `workflow-aggregator` (Pipeline) | `create_pipeline_job`, and `term`/`kill` on `stop_build` |
| `workflow-multibranch` + `branch-api` | `create_multibranch_pipeline`, `scan_multibranch_pipeline` |
| `git` | Multibranch jobs created against a Git source |

A missing plugin produces a Jenkins error on the affected tool only. The rest
keep working.

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
