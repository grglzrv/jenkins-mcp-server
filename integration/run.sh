#!/usr/bin/env bash
set -euo pipefail
# Starts TLS Jenkins, obtains an API token using the script console, starts MCP,
# then runs end-to-end calls via the official MCP Python client.

COMPOSE="docker compose -f docker-compose.integration.yml"

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
CRUMB_JSON=$(curl -sk -u admin:admin-test-password 'https://localhost:8443/crumbIssuer/api/json')
CRUMB_FIELD=$(printf '%s' "$CRUMB_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["crumbRequestField"])')
CRUMB=$(printf '%s' "$CRUMB_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["crumb"])')
TOKEN=$(curl -sk -u admin:admin-test-password -H "$CRUMB_FIELD: $CRUMB" -X POST 'https://localhost:8443/me/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken' --data 'newTokenName=mcp-integration' | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["tokenValue"])')
mkdir -p integration/shared-certs
CID=$(docker compose -f docker-compose.integration.yml ps -q jenkins)
docker cp "$CID:/var/jenkins_home/certs/ca.crt" integration/shared-certs/ca.crt
export JENKINS_TOKEN="$TOKEN"
docker compose -f docker-compose.integration.yml up -d --build mcp
step "Waiting for the MCP server to become ready"
for _ in {1..60}; do curl -sf http://localhost:8081/readyz >/dev/null && break; sleep 1; done
curl -sf http://localhost:8081/readyz >/dev/null || { echo "MCP server did not become ready within 60s"; exit 1; }
echo "::endgroup::"
python3 integration/test_mcp_http.py
