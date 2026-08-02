#!/usr/bin/env bash
set -euo pipefail
# Starts TLS Jenkins, obtains an API token using the script console, starts MCP,
# then runs end-to-end calls via the official MCP Python client.
docker compose -f docker-compose.integration.yml up -d --build jenkins
for _ in {1..90}; do curl -skf https://localhost:8443/login >/dev/null && break; sleep 2; done
CRUMB_JSON=$(curl -sk -u admin:admin-test-password 'https://localhost:8443/crumbIssuer/api/json')
CRUMB_FIELD=$(printf '%s' "$CRUMB_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["crumbRequestField"])')
CRUMB=$(printf '%s' "$CRUMB_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["crumb"])')
TOKEN=$(curl -sk -u admin:admin-test-password -H "$CRUMB_FIELD: $CRUMB" -X POST 'https://localhost:8443/me/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken' --data 'newTokenName=mcp-integration' | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["tokenValue"])')
mkdir -p integration/shared-certs
CID=$(docker compose -f docker-compose.integration.yml ps -q jenkins)
docker cp "$CID:/var/jenkins_home/certs/ca.crt" integration/shared-certs/ca.crt
export JENKINS_TOKEN="$TOKEN"
docker compose -f docker-compose.integration.yml up -d --build mcp
for _ in {1..60}; do curl -sf http://localhost:8081/readyz >/dev/null && break; sleep 1; done
curl -sf http://localhost:8081/readyz >/dev/null
python3 integration/test_mcp_http.py
