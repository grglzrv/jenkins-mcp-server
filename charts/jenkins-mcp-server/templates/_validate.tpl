{{/*
Cross-field validation that would otherwise only surface at runtime.
*/}}
{{- define "jenkins-mcp-server.validate" -}}
{{- if not .Values.jenkins.url }}
{{- fail "jenkins.url is required. Set it to the Jenkins base URL, including any path prefix, for example https://ci.example.com/jenkins." }}
{{- end }}

{{- /* Tailscale is optional. Configuring a sub-feature while the integration
       is off would silently render nothing. */ -}}
{{- if not .Values.tailscale.enabled }}
{{- $ts := list -}}
{{- if .Values.tailscale.egress.enabled }}{{- $ts = append $ts "egress.enabled" }}{{- end }}
{{- if .Values.tailscale.magicDNS.createDNSConfig }}{{- $ts = append $ts "magicDNS.createDNSConfig" }}{{- end }}
{{- if .Values.tailscale.proxyGroups.create }}{{- $ts = append $ts "proxyGroups.create" }}{{- end }}
{{- if $ts }}
{{- fail (printf "tailscale.enabled is false, so these would render nothing: %s. Set tailscale.enabled: true, or remove them." (join ", " $ts)) }}
{{- end }}
{{- end }}

{{- /* An egress proxy must point at the same host jenkins.url names, or TLS
       hostname verification fails against the certificate. */ -}}
{{- if and .Values.tailscale.enabled .Values.tailscale.egress.enabled }}
{{- if not .Values.tailscale.egress.tailnetFQDN }}
{{- fail "tailscale.egress.tailnetFQDN is required when the egress proxy is enabled. It must match the host in jenkins.url." }}
{{- end }}
{{- end }}

{{- /* Exactly one credential source. Four booleans replace what used to be a
       matrix of pairwise exclusions, so the failure names the count rather than
       one arbitrary conflict. */ -}}
{{- $creds := .Values.jenkins.credentials -}}
{{- $enabled := list -}}
{{- if $creds.existingSecret.enabled }}{{- $enabled = append $enabled "existingSecret" }}{{- end }}
{{- if $creds.secretKeyRefs.enabled }}{{- $enabled = append $enabled "secretKeyRefs" }}{{- end }}
{{- if $creds.create.enabled }}{{- $enabled = append $enabled "create" }}{{- end }}
{{- if $creds.externalSecret.enabled }}{{- $enabled = append $enabled "externalSecret" }}{{- end }}
{{- if gt (len $enabled) 1 }}
{{- fail (printf "exactly one jenkins.credentials source may be enabled, found %d: %s. Each resolves the username and token from a different place, so enabling several leaves the chart reading from a Secret you did not intend." (len $enabled) (join ", " $enabled)) }}
{{- end }}
{{- if eq (len $enabled) 0 }}
{{- fail "no jenkins.credentials source is enabled. The server cannot start without a username and API token. Enable one of: existingSecret (reference a Secret you created), secretKeyRefs (a different Secret or key per field), create (chart-managed, disposable environments only), externalSecret (External Secrets Operator)." }}
{{- end }}
{{- if $creds.create.enabled }}
{{- $canonicalUsername := default "" (index $creds.create "JENKINS_USERNAME") -}}
{{- $canonicalToken := default "" (index $creds.create "JENKINS_TOKEN") -}}
{{- $legacyUsername := default "" $creds.create.username -}}
{{- $legacyToken := default "" $creds.create.token -}}
{{- $legacyUsernameKey := default "" $creds.create.usernameKey -}}
{{- $legacyTokenKey := default "" $creds.create.tokenKey -}}
{{- if and (or $canonicalUsername $canonicalToken) (or $legacyUsername $legacyToken $legacyUsernameKey $legacyTokenKey) }}
{{- fail "jenkins.credentials.create mixes JENKINS_USERNAME/JENKINS_TOKEN with deprecated username/token/usernameKey/tokenKey values. Use only the uppercase Secret-key fields." }}
{{- end }}
{{- if or $canonicalUsername $canonicalToken }}
{{- $_ := required "jenkins.credentials.create.JENKINS_USERNAME is required when create.enabled=true" $canonicalUsername -}}
{{- $_ := required "jenkins.credentials.create.JENKINS_TOKEN is required when create.enabled=true" $canonicalToken -}}
{{- else }}
{{- $_ := required "jenkins.credentials.create.JENKINS_USERNAME is required when create.enabled=true (deprecated create.username is still accepted for 2.x compatibility)" $legacyUsername -}}
{{- $_ := required "jenkins.credentials.create.JENKINS_TOKEN is required when create.enabled=true (deprecated create.token is still accepted for 2.x compatibility)" $legacyToken -}}
{{- end }}
{{- end }}
{{- if and $creds.externalSecret.enabled $creds.externalSecret.dataFrom $creds.externalSecret.extraData }}
{{- fail "jenkins.credentials.externalSecret.dataFrom and extraData cannot be combined. dataFrom replaces the explicit data list, so extraData would be ignored; choose one source shape." }}
{{- end }}

{{- /* TLS trust settings. A CA bundle is only meaningful when verification is
       on, and only needed when the issuer is not publicly trusted. A publicly
       issued certificate (Let's Encrypt, Tailscale) needs no bundle at all. */ -}}
{{- $ca := .Values.jenkins.caBundle -}}
{{- if and (not .Values.jenkins.verifyTls) (or $ca.existingSecret .Values.jenkins.caBundlePath) }}
{{- fail "jenkins.verifyTls is false but a CA bundle is configured. A CA bundle only has meaning when verification is enabled. Remove the bundle to disable verification, or set verifyTls: true to verify against it." }}
{{- end }}
{{- if and $ca.existingSecret .Values.jenkins.caBundlePath }}
{{- fail (printf "jenkins.caBundlePath (%s) and jenkins.caBundle.existingSecret (%s) are both set. caBundlePath wins and the mounted Secret is ignored. Use one." .Values.jenkins.caBundlePath $ca.existingSecret) }}
{{- end }}

{{- /* minibridge settings do nothing unless the proxy is enabled. Silently
       ignoring guardrails or a tool policy is a security-relevant surprise. */ -}}
{{- if not .Values.minibridge.enabled }}
{{- $ignored := list -}}
{{- if .Values.minibridge.tools.deny }}{{- $ignored = append $ignored "tools.deny" }}{{- end }}
{{- if .Values.minibridge.tools.allow }}{{- $ignored = append $ignored "tools.allow" }}{{- end }}
{{- if .Values.minibridge.methodsDeny }}{{- $ignored = append $ignored "methodsDeny" }}{{- end }}
{{- if .Values.minibridge.guardrails }}{{- $ignored = append $ignored "guardrails" }}{{- end }}
{{- if .Values.minibridge.basicAuth.enabled }}{{- $ignored = append $ignored "basicAuth.enabled" }}{{- end }}
{{- if .Values.minibridge.tls.enabled }}{{- $ignored = append $ignored "tls.enabled" }}{{- end }}
{{- if .Values.minibridge.tls.passSecretKey }}{{- $ignored = append $ignored "tls.passSecretKey" }}{{- end }}
{{- if or .Values.minibridge.tls.pass.valueFrom.name .Values.minibridge.tls.pass.valueFrom.key }}{{- $ignored = append $ignored "tls.pass.valueFrom" }}{{- end }}
{{- if .Values.minibridge.policer.http.enabled }}{{- $ignored = append $ignored "policer.http.enabled" }}{{- end }}
{{- if $ignored }}
{{- fail (printf "minibridge.enabled is false, so these settings would be silently ignored: %s. Set minibridge.enabled: true to enforce them, or remove them. Note the server's own mcp.* policy still applies either way." (join ", " $ignored)) }}
{{- end }}
{{- end }}

{{- if .Values.minibridge.enabled }}
{{- $mb := .Values.minibridge -}}
{{- if and $mb.policer.rego.enabled $mb.policer.http.enabled }}
{{- fail "minibridge.policer.rego.enabled and minibridge.policer.http.enabled are mutually exclusive. Select exactly one policer." }}
{{- end }}
{{- if and (not $mb.policer.rego.enabled) (not $mb.policer.http.enabled) }}
{{- fail "minibridge requires a policer. Enable exactly one of minibridge.policer.rego.enabled or minibridge.policer.http.enabled." }}
{{- end }}
{{- if and $mb.policer.http.enabled (not $mb.policer.http.url) }}
{{- fail "minibridge.policer.http.url is required when the HTTP policer is enabled." }}
{{- end }}
{{- if and $mb.policer.http.token.existingSecret (not $mb.policer.http.token.secretKey) }}
{{- fail "minibridge.policer.http.token.secretKey is required when its existingSecret is set." }}
{{- end }}
{{- if and (not $mb.policer.http.enabled) (or $mb.policer.http.url $mb.policer.http.caPath $mb.policer.http.token.existingSecret) }}
{{- fail "minibridge.policer.http is disabled but its URL, CA, or token is configured. Enable the HTTP policer and disable the Rego policer, or clear those HTTP settings." }}
{{- end }}
{{- if and $mb.tls.passSecretKey $mb.tls.pass.valueFrom.name }}
{{- fail "minibridge.tls.passSecretKey and minibridge.tls.pass.valueFrom are mutually exclusive. Use the first for the TLS certificate Secret or valueFrom for a separate Secret." }}
{{- end }}
{{- if ne (not $mb.tls.pass.valueFrom.name) (not $mb.tls.pass.valueFrom.key) }}
{{- fail "minibridge.tls.pass.valueFrom requires both name and key." }}
{{- end }}
{{- if and (not $mb.tls.enabled) (or $mb.tls.existingSecret $mb.tls.passSecretKey $mb.tls.pass.valueFrom.name $mb.tls.pass.valueFrom.key $mb.tls.clientCASecretKey) }}
{{- fail "minibridge.tls is disabled but TLS Secret settings are configured. Enable TLS or clear those settings." }}
{{- end }}
{{- end }}

{{- /* An Ingress TLS secret is only read when TLS is on. */ -}}
{{- if and .Values.ingress.enabled .Values.ingress.tlsSecretName (not .Values.ingress.tls) }}
{{- fail "ingress.tlsSecretName is set but ingress.tls is false, so the secret would be ignored. Set ingress.tls: true or clear tlsSecretName." }}
{{- end }}

{{- /* PodDisruptionBudget and autoscaling must agree, or a voluntary
       disruption can never proceed at minimum scale. */ -}}
{{- if .Values.podDisruptionBudget.enabled }}
{{- $min := .Values.podDisruptionBudget.minAvailable -}}
{{- /* The floor is the autoscaler minimum when it owns the count, otherwise
       the static replica count. Either way minAvailable must sit below it, or
       no pod is ever evictable and a node drain blocks indefinitely. */ -}}
{{- $floor := .Values.replicaCount -}}
{{- if .Values.autoscaling.enabled }}{{- $floor = .Values.autoscaling.minReplicas -}}{{- end -}}
{{- /* --set yields float64, values.yaml yields int, and a percentage such as
       "50%" is a string that cannot be compared numerically. */ -}}
{{- if and $min (not (kindIs "string" $min)) }}
{{- if ge (int $min) (int $floor) }}
{{- fail (printf "podDisruptionBudget.minAvailable (%v) must be lower than the minimum pod count (%v, from %s), otherwise no pod can ever be evicted and node drains block indefinitely. Lower minAvailable, raise the replica count, or disable the PodDisruptionBudget." $min $floor (ternary "autoscaling.minReplicas" "replicaCount" .Values.autoscaling.enabled)) }}
{{- end }}
{{- end }}
{{- end }}
{{- if and .Values.minibridge.enabled .Values.minibridge.basicAuth.enabled }}
{{- $credName := include "jenkins-mcp-server.credentialsSecretName" . }}
{{- if and .Values.jenkins.credentials.externalSecret.enabled (eq .Values.minibridge.basicAuth.existingSecret $credName) }}
{{- $key := .Values.minibridge.basicAuth.secretKey }}
{{- $provided := list .Values.jenkins.credentials.externalSecret.targetUsernameKey .Values.jenkins.credentials.externalSecret.targetTokenKey }}
{{- range .Values.jenkins.credentials.externalSecret.extraData }}{{- $provided = append $provided .secretKey }}{{- end }}
{{- if and (not .Values.jenkins.credentials.externalSecret.dataFrom) (not (has $key $provided)) }}
{{- fail (printf "minibridge.basicAuth points at the ExternalSecret target %q for key %q, but the ExternalSecret only creates keys %v. The pod would fail with CreateContainerConfigError. Add the key via externalSecret.extraData, or point basicAuth.existingSecret at a different Secret." $credName $key $provided) }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}
