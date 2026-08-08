#!/usr/bin/env bash
# Dump the plugin versions a live Jenkins is running, in plugins.txt format.
#
# Used to pin the integration suite to a controller's real plugin set, which is
# the only way to test a core that current plugins no longer support: the plugin
# versions that work with it were installed when they were current, and the
# per-line update centre that served them has since been retired.
#
#   JENKINS_URL=https://jenkins.example.com \
#   JENKINS_USERNAME='<jenkins-ldap-user-id>' \
#   JENKINS_TOKEN=<api-token> \
#     ./scripts/dump_jenkins_plugins.sh > integration/jenkins/plugins.txt
#
# By default only the plugins the integration suite needs are emitted, plus the
# dependencies that reported version conflicts. Pass --all for every plugin.
set -euo pipefail

: "${JENKINS_URL:?set JENKINS_URL}"
: "${JENKINS_USERNAME:?set JENKINS_USERNAME}"
: "${JENKINS_TOKEN:?set JENKINS_TOKEN}"

ALL=0
[ "${1:-}" = "--all" ] && ALL=1

# Everything the suite installs, plus the transitive plugins that refused to
# resolve against an older core. Pinning the dependencies too keeps the resolver
# from pulling newer ones that reintroduce the conflict.
WANTED="configuration-as-code workflow-aggregator git branch-api
workflow-multibranch cloudbees-folder matrix-auth
workflow-cps workflow-job workflow-support workflow-basic-steps
workflow-scm-step workflow-api workflow-step-api workflow-durable-task-step
pipeline-model-api pipeline-model-definition pipeline-model-extensions
pipeline-stage-tags-metadata pipeline-groovy-lib
git-client scm-api credentials durable-task mailer structs"

RAW="$(curl -sS -f -u "${JENKINS_USERNAME}:${JENKINS_TOKEN}" \
  "${JENKINS_URL%/}/pluginManager/api/json?depth=1&tree=plugins\[shortName,version,enabled\]")"

WANTED="$WANTED" ALL="$ALL" python3 - "$RAW" <<'PY'
import json, os, sys

data = json.loads(sys.argv[1])
wanted = set(os.environ["WANTED"].split())
show_all = os.environ["ALL"] == "1"

rows = []
for p in data.get("plugins", []):
    name = p.get("shortName")
    if not name or not p.get("enabled", True):
        continue
    if show_all or name in wanted:
        rows.append((name, p.get("version", "")))

if not rows:
    sys.exit("no matching plugins found; is this the right controller?")

for name, version in sorted(rows):
    print(f"{name}:{version}" if version else name)

missing = sorted(wanted - {n for n, _ in rows}) if not show_all else []
if missing:
    print(f"\n# not installed on this controller: {', '.join(missing)}", file=sys.stderr)
    print("# tools depending on them will fail; see docs/JENKINS_COMPATIBILITY.md",
          file=sys.stderr)
PY
