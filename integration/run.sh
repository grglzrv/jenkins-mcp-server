#!/usr/bin/env bash
set -euo pipefail
# Starts TLS Jenkins, obtains an API token using the script console, starts MCP,
# then runs end-to-end calls via the official MCP Python client.

COMPOSE="docker compose -f docker-compose.integration.yml"

# Jenkins core under test. The compatibility matrix sets this; the normal suite
# uses the default baked into the compose file.
echo "Jenkins image under test: ${JENKINS_IMAGE:-jenkins/jenkins:lts-jdk21 (default)}"

# Without this a failure surfaces only as "exit code 1", with the actual cause
# locked inside the containers. Dump everything useful before tearing down.
on_failure() {
  local code=$?
  echo "::group::Integration failure diagnostics (exit ${code}, line ${BASH_LINENO[0]})"
  echo "--- compose ps ---";       $COMPOSE ps || true
  echo "--- jenkins logs (200) ---"; $COMPOSE logs --tail=200 jenkins || true
  echo "--- mcp logs (200) ---";     $COMPOSE logs --tail=200 mcp || true
  echo "--- jenkins /login ---";   curl -sk -o /dev/null -w 'HTTP %{http_code}\n' https://localhost:8443/login || true
  echo "--- mcp /readyz ---";      curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8081/readyz || true
  echo "::endgroup::"
  exit "$code"
}
trap on_failure ERR

step() { echo "::group::$*"; }
docker compose -f docker-compose.integration.yml up -d --build jenkins
step "Waiting for Jenkins to become reachable"
for _ in {1..90}; do curl -skf https://localhost:8443/login >/dev/null && break; sleep 2; done
curl -skf https://localhost:8443/login >/dev/null || { echo "Jenkins did not come up within 180s"; exit 1; }
echo "::endgroup::"
JENKINS_LOCAL="https://localhost:8443"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

# -L because /me/... redirects to /user/<id>/...; without it the POST returns an
# empty body. The cookie jar keeps the crumb and the request on one session,
# which Jenkins requires when authenticating with a password rather than a token.
jcurl() {
  curl -sk -L -b "$COOKIE_JAR" -c "$COOKIE_JAR" -u admin:admin-test-password "$@"
}

# Fails with the actual payload instead of a bare JSONDecodeError.
json_field() {
  local raw="$1"; shift
  printf '%s' "$raw" | python3 -c '
import sys, json
raw = sys.stdin.read()
if not raw.strip():
    sys.exit("Jenkins returned an empty response where JSON was expected")
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    sys.exit("Jenkins returned non-JSON: " + raw[:400].replace("\n", " "))
for key in sys.argv[1:]:
    try:
        data = data[key]
    except (KeyError, TypeError):
        sys.exit("key %r missing from Jenkins response: %s" % (key, json.dumps(data)[:400]))
print(data)
' "$@"
}

step "Waiting for the Jenkins security realm to accept authentication"
# /login answers before JCasC has finished creating the admin user, so poll an
# authenticated endpoint rather than the login page.
for _ in {1..60}; do
  [ "$(jcurl -o /dev/null -w '%{http_code}' "$JENKINS_LOCAL/me/api/json")" = "200" ] && break
  sleep 2
done
AUTH_CODE="$(jcurl -o /dev/null -w '%{http_code}' "$JENKINS_LOCAL/me/api/json")"
if [ "$AUTH_CODE" != "200" ]; then
  echo "Jenkins did not accept admin authentication within 120s (last HTTP $AUTH_CODE)"
  exit 1
fi
echo "::endgroup::"

step "Generating a Jenkins API token"
CRUMB_JSON="$(jcurl "$JENKINS_LOCAL/crumbIssuer/api/json")"
CRUMB_FIELD="$(json_field "$CRUMB_JSON" crumbRequestField)"
CRUMB="$(json_field "$CRUMB_JSON" crumb)"
USER_ID="$(json_field "$(jcurl "$JENKINS_LOCAL/me/api/json")" id)"
TOKEN_JSON="$(jcurl -H "$CRUMB_FIELD: $CRUMB" -X POST \
  "$JENKINS_LOCAL/user/$USER_ID/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken" \
  --data 'newTokenName=mcp-integration')"
TOKEN="$(json_field "$TOKEN_JSON" data tokenValue)"
echo "::endgroup::"
mkdir -p integration/shared-certs
CID=$(docker compose -f docker-compose.integration.yml ps -q jenkins)
docker cp "$CID:/var/jenkins_home/certs/ca.crt" integration/shared-certs/ca.crt
export JENKINS_TOKEN="$TOKEN"
# --no-deps is essential. Without it compose rebuilds and recreates the jenkins
# service too, and because that image runs apt-get update its ID changes on
# every build, so Jenkins restarts here and answers 503 while it boots.
docker compose -f docker-compose.integration.yml up -d --build --no-deps mcp
step "Waiting for the MCP server to become ready"
for _ in {1..60}; do curl -sf http://localhost:8081/readyz >/dev/null && break; sleep 1; done
curl -sf http://localhost:8081/readyz >/dev/null || { echo "MCP server did not become ready within 60s"; exit 1; }

# /readyz does not actively probe Jenkins. Before normal traffic it has no
# contact result, so confirm Jenkins is serving before exercising any tool.
for _ in {1..60}; do jcurl -o /dev/null -w '%{http_code}' "$JENKINS_LOCAL/api/json" | grep -q '^200$' && break; sleep 2; done
JENKINS_CODE="$(jcurl -o /dev/null -w '%{http_code}' "$JENKINS_LOCAL/api/json")"
if [ "$JENKINS_CODE" != "200" ]; then
  echo "Jenkins is not serving requests before the MCP calls (last HTTP $JENKINS_CODE)"
  exit 1
fi
echo "::endgroup::"
python3 integration/test_mcp_http.py

step "Verifying passive Jenkins diagnostics"
curl -fsS http://localhost:8081/readyz | python3 -c '
import json, sys
payload = json.load(sys.stdin)
jenkins = payload["jenkins"]
assert jenkins["last_contact_age_seconds"] is not None, jenkins
assert jenkins["last_transport_error"] is None, jenkins
print(jenkins)
'
