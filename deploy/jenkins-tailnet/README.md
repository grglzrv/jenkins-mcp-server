# Jenkins on the tailnet

This setup keeps Jenkins private and publishes it as the Tailscale Service `svc:jenkins` on HTTPS port 443.

## Prerequisites

1. Enable MagicDNS and HTTPS certificates in the tailnet.
2. Define a Tailscale Service named `jenkins` with endpoint `tcp:443`.
3. Create `tag:jenkins` and allow it to host/auto-approve `svc:jenkins`.
4. Create a tagged auth key scoped to `tag:jenkins` and store it in a root-readable file.
5. Ensure Jenkins listens locally on `127.0.0.1:8080` or `127.0.0.1:8443`.

## Install and advertise

```bash
sudo install -m 600 /dev/stdin /run/secrets/tailscale-authkey <<'EOF'
tskey-auth-REPLACE_ME
EOF

sudo TS_AUTHKEY_FILE=/run/secrets/tailscale-authkey \
  TS_HOSTNAME=jenkins-ci \
  TS_TAGS=tag:jenkins \
  TS_SERVICE=svc:jenkins \
  JENKINS_BACKEND=https+insecure://127.0.0.1:8443 \
  ./install-and-advertise.sh
```

Use `https://127.0.0.1:8443` instead of `https+insecure` when the local Jenkins certificate is valid for that connection, or use `http://127.0.0.1:8080` if Jenkins is only bound to loopback HTTP. Tailscale still presents a valid tailnet HTTPS certificate externally.

The resulting service FQDN becomes the value of `JENKINS_URL` in the MCP deployment and the `tailscale.com/tailnet-fqdn` annotation in `jenkins-egress-service.yaml`.

Set **Manage Jenkins → System → Jenkins Location → Jenkins URL** to the final Tailscale Service URL. This prevents redirects and generated links from pointing to the loopback/backend listener.
