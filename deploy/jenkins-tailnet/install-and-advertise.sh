#!/usr/bin/env bash
set -Eeuo pipefail

# Required:
#   TS_AUTHKEY_FILE=/run/secrets/tailscale-authkey
# The auth key should be reusable/ephemeral only when appropriate, pre-approved,
# and scoped to tag:jenkins.
: "${TS_AUTHKEY_FILE:?Set TS_AUTHKEY_FILE to a root-readable file containing a Tailscale auth key}"

TS_HOSTNAME="${TS_HOSTNAME:-jenkins-ci}"
TS_TAGS="${TS_TAGS:-tag:jenkins}"
TS_SERVICE="${TS_SERVICE:-svc:jenkins}"
JENKINS_BACKEND="${JENKINS_BACKEND:-https+insecure://127.0.0.1:8443}"

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

sudo systemctl enable --now tailscaled
sudo tailscale up \
  --auth-key="file:${TS_AUTHKEY_FILE}" \
  --hostname="${TS_HOSTNAME}" \
  --advertise-tags="${TS_TAGS}" \
  --accept-dns=false

# A real Tailscale Service named svc:jenkins must already be defined in the
# Tailscale admin console. HTTPS is terminated by Tailscale and forwarded to
# the local Jenkins listener. Use https://127.0.0.1:8443 when Jenkins has a
# trusted local certificate, https+insecure://... for a self-signed local cert,
# or http://127.0.0.1:8080 for a local HTTP-only Jenkins listener.
sudo tailscale serve \
  --service="${TS_SERVICE}" \
  --https=443 \
  "${JENKINS_BACKEND}"

sudo tailscale status
sudo tailscale serve status --json
