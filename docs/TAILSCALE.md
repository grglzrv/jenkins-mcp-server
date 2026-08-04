# Tailscale: finding your domain and wiring it up

Every `example-tailnet.ts.net` in this repository is a placeholder. This page
explains what that value actually is, where to find yours, and which fields it
has to be copied into.

## What the domain is

Tailscale gives each private network — a *tailnet* — a DNS name. Every name you
will use here ends with it.

When a tailnet is created it gets a generated name of the form `tail<hex>.ts.net`,
for example `tail4f2a.ts.net`. You can swap that for a friendlier randomized pair
of words such as `cat-crocodile.ts.net`. Both are equally valid; the words are
just easier to type.

Two kinds of name are built from it:

| Kind | Shape | Example |
| --- | --- | --- |
| A device | `<machine-name>.<tailnet-name>` | `jenkins-ci.cat-crocodile.ts.net` |
| A Tailscale Service | `<service-name>.<tailnet-name>` | `jenkins.cat-crocodile.ts.net` |

This repository uses the **Service** form for Jenkins. A Service name is stable
and independent of which machine currently hosts it, so Jenkins can move hosts
without `JENKINS_URL` changing.

## Finding yours

**In the admin console.** Open the [DNS page](https://login.tailscale.com/admin/dns).
The tailnet DNS name is shown at the top. This is also where you rename it, and
where MagicDNS and HTTPS certificates are enabled.

**From any machine already on the tailnet:**

```bash
tailscale status --json | jq -r .MagicDNSSuffix
# cat-crocodile.ts.net

# or, on newer clients
tailscale dns status | grep -i suffix
```

**From the machine hosting Jenkins**, to see the full name it is serving:

```bash
tailscale serve status
# https://jenkins.cat-crocodile.ts.net:443/  <-- this is JENKINS_URL
#   |-- proxy https+insecure://127.0.0.1:8443
```

If `tailscale serve status` prints nothing, Jenkins is not being advertised yet.
See [deploy/jenkins-tailnet](../deploy/jenkins-tailnet/README.md).

## Prerequisites

Both are toggles on the DNS page of the admin console, and both are required:

- **MagicDNS** — without it the names above do not resolve at all.
- **HTTPS certificates** — without it Tailscale cannot issue the certificate for
  `jenkins.<tailnet>.ts.net`, so `JENKINS_VERIFY_TLS=true` will fail. Do not work
  around this by setting it to `false`; fix the certificate instead.

## Where the value goes

Assume your tailnet is `cat-crocodile.ts.net` and Jenkins is advertised as the
Service `svc:jenkins`. The Jenkins FQDN is therefore
`jenkins.cat-crocodile.ts.net`.

### Helm values

```yaml
jenkins:
  # Must be the exact FQDN. Anything else fails TLS hostname validation.
  url: https://jenkins.cat-crocodile.ts.net

tailscale:
  egress:
    enabled: true
    # The same FQDN again. This tells the Operator which tailnet target to
    # route to; the pod still connects using jenkins.url above, so the
    # certificate hostname is preserved.
    tailnetFQDN: jenkins.cat-crocodile.ts.net
    proxyGroup: jenkins-egress

ingress:
  enabled: true
  className: tailscale
  # Only the machine name, not the FQDN. Tailscale appends the tailnet name,
  # so this produces https://jenkins-mcp.cat-crocodile.ts.net
  hostname: jenkins-mcp
```

### Raw manifests

| File | Field |
| --- | --- |
| `deploy/kubernetes/base/config.env` | `JENKINS_URL` |
| `deploy/kubernetes/tailscale/jenkins-egress-service.yaml` | `tailscale.com/tailnet-fqdn` annotation |
| `deploy/kubernetes/tailscale/ingress.yaml` | `spec.tls[].hosts[]` (machine name only) |

The distinction that catches people out: **`jenkins.url` and `tailnetFQDN` take
the full FQDN, the Ingress hostname takes only the machine name.** Tailscale
appends the tailnet suffix to the Ingress hostname itself, so putting the FQDN
there yields `jenkins-mcp.cat-crocodile.ts.net.cat-crocodile.ts.net`.

## The resulting MCP URL

Once deployed, the MCP server is reachable from anywhere on the tailnet at:

```
https://jenkins-mcp.cat-crocodile.ts.net/mcp
```

Confirm the name the Operator actually assigned rather than assuming:

```bash
kubectl get ingress -n jenkins-mcp jenkins-mcp -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

## Verifying

```bash
# 1. The Jenkins Service resolves and serves a valid certificate
curl -sI https://jenkins.cat-crocodile.ts.net/login | head -1

# 2. The MCP pod can reach Jenkins through the egress proxy
kubectl exec -n jenkins-mcp deploy/jenkins-mcp -- \
  python -c "import os,httpx;print(httpx.get(os.environ['JENKINS_URL']+'/login').status_code)"

# 3. The MCP endpoint answers from a tailnet device
curl -sI https://jenkins-mcp.cat-crocodile.ts.net/mcp | head -1
```

## Common mistakes

| Symptom | Cause |
| --- | --- |
| `certificate is valid for X, not Y` | `JENKINS_URL` is not the exact Service FQDN, or an IP is being used |
| Name does not resolve inside the cluster | CoreDNS is not forwarding the `ts.net` zone to the `DNSConfig` nameserver IP |
| Name resolves everywhere except in-cluster | The `DNSConfig` was applied but CoreDNS was never restarted |
| Ingress hostname has the suffix twice | The FQDN was used where only the machine name belongs |
| TLS handshake fails against Jenkins | HTTPS certificates are not enabled on the tailnet |

For the cluster-side pieces — ProxyGroups, DNSConfig, CoreDNS — see
[KUBERNETES_TAILSCALE_ARGOCD.md](KUBERNETES_TAILSCALE_ARGOCD.md).
