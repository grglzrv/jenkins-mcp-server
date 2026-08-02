#!/bin/sh
# Entrypoint for the minibridge-wrapped Jenkins MCP server.
#
# minibridge terminates MCP over HTTP, applies the Rego guardrail policy to
# every request and response, and speaks stdio to the Jenkins MCP server it
# spawns as a child. The Python server therefore runs in stdio mode here and
# never listens on a socket itself.
set -eu

fail() {
	echo "!!! Error: jenkins-mcp-server requires $1 to be set." >&2
	exit 1
}

[ -z "${JENKINS_URL:-}" ] && fail "JENKINS_URL"
[ -z "${JENKINS_USERNAME:-}" ] && fail "JENKINS_USERNAME"
# Injected from a Kubernetes Secret, which External Secrets may populate from
# GCP Secret Manager. The token itself is never baked into the image.
[ -z "${JENKINS_TOKEN:-}" ] && fail "JENKINS_TOKEN"

# aio = HTTP frontend plus the spawned stdio backend in one process.
if [ -z "${MINIBRIDGE_MODE:-}" ]; then
	if [ -p /dev/stdin ]; then
		MINIBRIDGE_MODE=aio
	else
		export MINIBRIDGE_LISTEN="${MINIBRIDGE_LISTEN:-:8000}"
		MINIBRIDGE_MODE=aio
	fi
fi

MINIBRIDGE_SBOM="${MINIBRIDGE_SBOM:-/sbom.json}"
if [ -n "$MINIBRIDGE_SBOM" ] && [ -s "$MINIBRIDGE_SBOM" ]; then
	export MINIBRIDGE_SBOM
else
	unset MINIBRIDGE_SBOM
fi

MINIBRIDGE_POLICER_REGO_POLICY="${MINIBRIDGE_POLICER_REGO_POLICY:-/policy.rego}"
if [ -n "$MINIBRIDGE_POLICER_REGO_POLICY" ] && [ -s "$MINIBRIDGE_POLICER_REGO_POLICY" ]; then
	export MINIBRIDGE_POLICER_TYPE="${MINIBRIDGE_POLICER_TYPE:-rego}"
	export MINIBRIDGE_POLICER_REGO_POLICY
else
	unset MINIBRIDGE_POLICER_REGO_POLICY
	if [ "${MINIBRIDGE_POLICER_TYPE:-}" = "rego" ]; then
		unset MINIBRIDGE_POLICER_TYPE
	fi
fi

export MINIBRIDGE_POLICER_ENFORCE="${MINIBRIDGE_POLICER_ENFORCE:-true}"
export MINIBRIDGE_HEALTH_LISTEN="${MINIBRIDGE_HEALTH_LISTEN:-:8080}"

# The policy reads these without the REGO_POLICY_RUNTIME_ prefix.
export REGO_POLICY_RUNTIME_GUARDRAILS="${GUARDRAILS:-}"
export REGO_POLICY_RUNTIME_BASIC_AUTH_SECRET="${BASIC_AUTH_SECRET:-}"
# Tool and capability policy. Empty means everything is allowed.
export REGO_POLICY_RUNTIME_TOOLS_DENY="${TOOLS_DENY:-}"
export REGO_POLICY_RUNTIME_TOOLS_ALLOW="${TOOLS_ALLOW:-}"
export REGO_POLICY_RUNTIME_METHODS_DENY="${METHODS_DENY:-}"

if grep -qE 'tmpfs.* /tmp ' /proc/mounts 2>/dev/null; then
	export MINIBRIDGE_MCP_USE_TEMPDIR="true"
fi

export MINIBRIDGE_OAUTH_DISABLED="${MINIBRIDGE_OAUTH_DISABLED:-true}"

# Force stdio on the child: minibridge owns the network listener.
if [ "$#" -gt 0 ]; then
	exec minibridge "${MINIBRIDGE_MODE}" -- jenkins-mcp-server --transport stdio "$@"
else
	exec minibridge "${MINIBRIDGE_MODE}" -- jenkins-mcp-server --transport stdio
fi
