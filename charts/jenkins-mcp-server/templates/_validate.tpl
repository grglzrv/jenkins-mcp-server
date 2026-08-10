{{/*
Cross-field validation that would otherwise only surface at runtime.
*/}}
{{- define "jenkins-mcp-server.validate" -}}
{{- if not .Values.jenkins.url }}
{{- fail "jenkins.url is required. Set it to the Jenkins base URL, including any path prefix, for example https://ci.example.com/jenkins." }}
{{- end }}

{{- /* The preStop delay consumes the same grace-period budget as application
       shutdown. Refuse a setting that guarantees SIGKILL before the process
       receives any time to handle SIGTERM. Zero explicitly disables it. */ -}}
{{- if and (gt (int .Values.preStopDelaySeconds) 0) (ge (int .Values.preStopDelaySeconds) (int .Values.terminationGracePeriodSeconds)) }}
{{- fail "preStopDelaySeconds must be less than terminationGracePeriodSeconds, because the hook consumes that grace period before SIGTERM is sent. Increase the grace period or set preStopDelaySeconds=0 to disable the delay." }}
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
{{- $url := urlParse .Values.jenkins.url -}}
{{- $jenkinsHost := regexReplaceAll ":[0-9]+$" (get $url "host") "" -}}
{{- if ne $jenkinsHost .Values.tailscale.egress.tailnetFQDN }}
{{- fail (printf "tailscale.egress.tailnetFQDN (%s) must exactly match the host in jenkins.url (%s), otherwise traffic or TLS hostname verification targets the wrong Jenkins controller." .Values.tailscale.egress.tailnetFQDN $jenkinsHost) }}
{{- end }}
{{- end }}

{{- /* Tailscale Ingress derives its hostname and certificate from spec.tls and
       expects no host rule or user-supplied TLS Secret. */ -}}
{{- if and .Values.ingress.enabled (eq .Values.ingress.className "tailscale") }}
{{- if not .Values.ingress.tls }}
{{- fail "ingress.className=tailscale requires ingress.tls=true because the Tailscale Operator derives the MagicDNS name and certificate from spec.tls.hosts." }}
{{- end }}
{{- if .Values.ingress.tlsSecretName }}
{{- fail "ingress.tlsSecretName must be empty for ingress.className=tailscale; the Tailscale Operator provisions the certificate." }}
{{- end }}
{{- if eq .Values.ingress.hostRule true }}
{{- fail "ingress.hostRule must be false or null for ingress.className=tailscale; the Operator expects a hostless rule." }}
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
{{- fail (printf "exactly one jenkins.credentials source may be enabled, found %d: %s. Each resolves the Jenkins user ID and API token from a different place, so enabling several leaves the chart reading from a Secret you did not intend." (len $enabled) (join ", " $enabled)) }}
{{- end }}
{{- if eq (len $enabled) 0 }}
{{- fail "no jenkins.credentials source is enabled. The server cannot start without a username and API token. Enable one of: existingSecret (reference a Secret you created), secretKeyRefs (a different Secret or key per field), create (chart-managed, disposable environments only), externalSecret (External Secrets Operator)." }}
{{- end }}
{{- /* Explicit env entries are rendered after the chart-owned credential,
       policy, and Minibridge variables, so a duplicate silently wins at
       runtime. Keep extraEnv genuinely extra instead of letting it bypass the
       validated values and credential-source selection above. */ -}}
{{- $extraEnvNames := list -}}
{{- $extraEnvExact := list "OTEL_EXPORTER_OTLP_ENDPOINT" "TOOLS_DENY" "TOOLS_ALLOW" "METHODS_DENY" "GUARDRAILS" "BASIC_AUTH_SECRET" -}}
{{- range $i, $entry := .Values.mcp.extraEnv }}
{{- $name := required (printf "mcp.extraEnv[%d].name is required" $i) $entry.name -}}
{{- if has $name $extraEnvNames }}
{{- fail (printf "mcp.extraEnv[%d].name (%s) duplicates another extraEnv entry; environment variable names must be unique." $i $name) }}
{{- end }}
{{- /* Compare upper-cased so mixed-case spellings cannot evade this boundary.
       Server aliases use the JENKINS_/MCP_ prefixes. Minibridge owns both its
       prefixed variables and the exact policy/auth names above. The canonical
       uppercase spelling would replace the chart value; any other spelling is
       inert in the case-sensitive runtimes and should fail instead of silently
       suggesting that the requested policy was applied. */ -}}
{{- $upper := upper $name -}}
{{- if or (hasPrefix "JENKINS_" $upper) (hasPrefix "MCP_" $upper) (hasPrefix "MINIBRIDGE_" $upper) (has $upper $extraEnvExact) }}
{{- fail (printf "mcp.extraEnv[%d].name (%s) is managed by the chart and cannot be set through extraEnv, in any capitalisation. Use the corresponding jenkins.*, mcp.*, audit.*, or minibridge.* value instead." $i $name) }}
{{- end }}
{{- $extraEnvNames = append $extraEnvNames $name -}}
{{- end }}
{{- if $creds.create.enabled }}
{{- $userId := default "" $creds.create.jenkinsUserId -}}
{{- $apiToken := default "" $creds.create.jenkinsApiToken -}}
{{- $uppercaseUserId := default "" (index $creds.create "JENKINS_USERNAME") -}}
{{- $uppercaseApiToken := default "" (index $creds.create "JENKINS_TOKEN") -}}
{{- $legacyUsername := default "" $creds.create.username -}}
{{- $legacyToken := default "" $creds.create.token -}}
{{- $legacyUsernameKey := default "" $creds.create.usernameKey -}}
{{- $legacyTokenKey := default "" $creds.create.tokenKey -}}
{{- if and (or $userId $apiToken) (or $uppercaseUserId $uppercaseApiToken $legacyUsername $legacyToken $legacyUsernameKey $legacyTokenKey) }}
{{- fail "jenkins.credentials.create mixes jenkinsUserId/jenkinsApiToken with deprecated credential fields. Use only jenkinsUserId and jenkinsApiToken." }}
{{- end }}
{{- if and (or $uppercaseUserId $uppercaseApiToken) (or $legacyUsername $legacyToken $legacyUsernameKey $legacyTokenKey) }}
{{- fail "jenkins.credentials.create mixes deprecated JENKINS_USERNAME/JENKINS_TOKEN with older username/token/usernameKey/tokenKey values. Use only jenkinsUserId and jenkinsApiToken." }}
{{- end }}
{{- if or $userId $apiToken }}
{{- $_ := required "jenkins.credentials.create.jenkinsUserId is required when create.enabled=true; use the exact value matching {0} in Jenkins' LDAP User search filter" $userId -}}
{{- $_ := required "jenkins.credentials.create.jenkinsApiToken is required when create.enabled=true; it must be generated by the configured Jenkins user ID" $apiToken -}}
{{- else if or $uppercaseUserId $uppercaseApiToken }}
{{- $_ := required "jenkins.credentials.create.jenkinsUserId is required when create.enabled=true (deprecated JENKINS_USERNAME is still accepted for 2.x compatibility)" $uppercaseUserId -}}
{{- $_ := required "jenkins.credentials.create.jenkinsApiToken is required when create.enabled=true (deprecated JENKINS_TOKEN is still accepted for 2.x compatibility)" $uppercaseApiToken -}}
{{- else }}
{{- $_ := required "jenkins.credentials.create.jenkinsUserId is required when create.enabled=true (deprecated username is still accepted for 2.x compatibility)" $legacyUsername -}}
{{- $_ := required "jenkins.credentials.create.jenkinsApiToken is required when create.enabled=true (deprecated token is still accepted for 2.x compatibility)" $legacyToken -}}
{{- end }}
{{- end }}
{{- if and $creds.existingSecret.enabled (eq $creds.existingSecret.usernameKey $creds.existingSecret.tokenKey) }}
{{- fail "jenkins.credentials.existingSecret.usernameKey and tokenKey must be different. Pointing both environment variables at one Secret key makes Jenkins receive the same value as the user ID and API token." }}
{{- end }}
{{- if and $creds.secretKeyRefs.enabled (eq $creds.secretKeyRefs.username.name $creds.secretKeyRefs.token.name) (eq $creds.secretKeyRefs.username.key $creds.secretKeyRefs.token.key) }}
{{- fail "jenkins.credentials.secretKeyRefs.username and token must not point at the same Secret key. Jenkins needs a distinct user ID and API token." }}
{{- end }}
{{- if and $creds.create.enabled (not (or $creds.create.jenkinsUserId $creds.create.jenkinsApiToken (index $creds.create "JENKINS_USERNAME") (index $creds.create "JENKINS_TOKEN"))) (eq (default "JENKINS_USERNAME" $creds.create.usernameKey) (default "JENKINS_TOKEN" $creds.create.tokenKey)) }}
{{- fail "deprecated jenkins.credentials.create.usernameKey and tokenKey must be different. Use jenkinsUserId and jenkinsApiToken with the stable chart-managed key names." }}
{{- end }}
{{- if and $creds.externalSecret.enabled (eq $creds.externalSecret.targetUsernameKey $creds.externalSecret.targetTokenKey) }}
{{- fail "jenkins.credentials.externalSecret.targetUsernameKey and targetTokenKey must be different. ESO must write the Jenkins user ID and API token to separate target Secret keys." }}
{{- end }}
{{- if and $creds.externalSecret.enabled $creds.externalSecret.dataFrom $creds.externalSecret.extraData }}
{{- fail "jenkins.credentials.externalSecret.dataFrom and extraData cannot be combined. dataFrom replaces the explicit data list, so extraData would be ignored; choose one source shape." }}
{{- end }}
{{- if and $creds.externalSecret.enabled (not $creds.externalSecret.dataFrom) (eq $creds.externalSecret.usernameRemoteKey $creds.externalSecret.tokenRemoteKey) }}
{{- if or (not $creds.externalSecret.usernameRemoteProperty) (not $creds.externalSecret.tokenRemoteProperty) (eq $creds.externalSecret.usernameRemoteProperty $creds.externalSecret.tokenRemoteProperty) }}
{{- fail "jenkins.credentials.externalSecret may use the same usernameRemoteKey and tokenRemoteKey only when usernameRemoteProperty and tokenRemoteProperty are both set and different." }}
{{- end }}
{{- end }}
{{- if and $creds.externalSecret.enabled (not $creds.externalSecret.dataFrom) }}
{{- $targetKeys := list $creds.externalSecret.targetUsernameKey $creds.externalSecret.targetTokenKey -}}
{{- range $i, $entry := $creds.externalSecret.extraData }}
{{- $key := required (printf "jenkins.credentials.externalSecret.extraData[%d].secretKey is required" $i) $entry.secretKey -}}
{{- if has $key $targetKeys }}
{{- fail (printf "jenkins.credentials.externalSecret.extraData[%d].secretKey (%s) duplicates another target key; every generated Secret key must be unique." $i $key) }}
{{- end }}
{{- $targetKeys = append $targetKeys $key -}}
{{- end }}
{{- end }}

{{- /* ESO rejects policy combinations where deletion requires ownership that
       the selected creation policy does not provide. */ -}}
{{- if $creds.externalSecret.enabled }}
{{- $creation := $creds.externalSecret.creationPolicy -}}
{{- $deletion := $creds.externalSecret.deletionPolicy -}}
{{- if and (eq $creds.externalSecret.apiVersion "external-secrets.io/v1beta1") (eq $creation "CreateOrMerge") }}
{{- fail "jenkins.credentials.externalSecret.creationPolicy=CreateOrMerge requires apiVersion=external-secrets.io/v1; the v1beta1 CRD does not define that policy." }}
{{- end }}
{{- if and (eq $deletion "Delete") (or (eq $creation "Merge") (eq $creation "None") (eq $creation "CreateOrMerge")) }}
{{- fail "jenkins.credentials.externalSecret.deletionPolicy=Delete requires creationPolicy=Owner or Orphan; the selected creation policy does not own a target Secret that ESO may delete." }}
{{- end }}
{{- if and (eq $deletion "Merge") (eq $creation "None") }}
{{- fail "jenkins.credentials.externalSecret.deletionPolicy=Merge cannot be combined with creationPolicy=None because there is no target Secret to merge." }}
{{- end }}
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

{{- /* Audit readiness and rotation have no effect without file output. The
       rotation pair must be enabled or disabled together. */ -}}
{{- if and .Values.audit.requiredForReadiness (not .Values.audit.fileEnabled) }}
{{- fail "audit.requiredForReadiness=true requires audit.fileEnabled=true; there is no audit file to require otherwise." }}
{{- end }}
{{- if ne (gt (int .Values.audit.maxFileBytes) 0) (gt (int .Values.audit.backupCount) 0) }}
{{- fail "audit.maxFileBytes and audit.backupCount must either both be zero (rotation disabled) or both be positive." }}
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
{{- if eq (int $mb.port) (int $mb.healthPort) }}
{{- fail "minibridge.port and minibridge.healthPort must be different because the proxy cannot bind both listeners to one port." }}
{{- end }}
{{- if and $mb.policer.rego.enabled $mb.policer.http.enabled }}
{{- fail "minibridge.policer.rego.enabled and minibridge.policer.http.enabled are mutually exclusive. Select exactly one policer." }}
{{- end }}
{{- if and (not $mb.policer.rego.enabled) (not $mb.policer.http.enabled) }}
{{- fail "minibridge requires a policer. Enable exactly one of minibridge.policer.rego.enabled or minibridge.policer.http.enabled." }}
{{- end }}
{{- if and $mb.policer.http.enabled (not $mb.policer.http.url) }}
{{- fail "minibridge.policer.http.url is required when the HTTP policer is enabled." }}
{{- end }}
{{- if and $mb.policer.http.enabled $mb.policer.http.token.existingSecret (not $mb.policer.http.token.secretKey) }}
{{- fail "minibridge.policer.http.token.secretKey is required when its existingSecret is set." }}
{{- end }}
{{- if and $mb.policer.http.enabled $mb.policer.http.token.secretKey (not $mb.policer.http.token.existingSecret) }}
{{- fail "minibridge.policer.http.token.existingSecret is required when its secretKey is set." }}
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
{{- if $mb.tls.enabled }}
{{- $_ := required "minibridge.tls.existingSecret is required when TLS is enabled" $mb.tls.existingSecret -}}
{{- $_ := required "minibridge.tls.certKey is required when TLS is enabled" $mb.tls.certKey -}}
{{- $_ := required "minibridge.tls.keyKey is required when TLS is enabled" $mb.tls.keyKey -}}
{{- if eq $mb.tls.certKey $mb.tls.keyKey }}
{{- fail "minibridge.tls.certKey and keyKey must be different." }}
{{- end }}
{{- end }}
{{- if and $mb.policer.rego.enabled (not $mb.policer.rego.policy) }}
{{- fail "minibridge.policer.rego.policy is required when the Rego policer is enabled." }}
{{- end }}
{{- if $mb.basicAuth.enabled }}
{{- $_ := required "minibridge.basicAuth.existingSecret is required when basicAuth is enabled" $mb.basicAuth.existingSecret -}}
{{- $_ := required "minibridge.basicAuth.secretKey is required when basicAuth is enabled" $mb.basicAuth.secretKey -}}
{{- end }}
{{- end }}

{{- if and (not .Values.minibridge.enabled) (eq (int .Values.mcp.port) (int .Values.mcp.healthPort)) }}
{{- fail "mcp.port and mcp.healthPort must be different because the server cannot bind both listeners to one port." }}
{{- end }}
{{- $effectiveHealthPort := .Values.mcp.healthPort -}}
{{- if .Values.minibridge.enabled }}{{- $effectiveHealthPort = .Values.minibridge.healthPort -}}{{- end }}
{{- if and .Values.service.exposeHealthPort (eq (int .Values.service.port) (int $effectiveHealthPort)) }}
{{- fail "service.port must differ from the effective health port when service.exposeHealthPort=true; Kubernetes Service ports must be unique." }}
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
{{- if and .Values.autoscaling.enabled (lt (int .Values.autoscaling.maxReplicas) (int .Values.autoscaling.minReplicas)) }}
{{- fail "autoscaling.maxReplicas must be greater than or equal to autoscaling.minReplicas." }}
{{- end }}
{{- if and .Values.audit.fileEnabled (not .Values.audit.path) }}
{{- fail "audit.path is required when audit.fileEnabled=true." }}
{{- end }}

{{- /* User-supplied pod metadata must not replace selector labels or disable
       the checksums that trigger safe rollouts. */ -}}
{{- range $key := list "app.kubernetes.io/name" "app.kubernetes.io/instance" "app.kubernetes.io/component" }}
{{- if hasKey $.Values.podLabels $key }}
{{- fail (printf "podLabels.%s is reserved by the chart and cannot be overridden." $key) }}
{{- end }}
{{- end }}
{{- range $key := list "checksum/config" "checksum/credentials" }}
{{- if hasKey $.Values.podAnnotations $key }}
{{- fail (printf "podAnnotations.%s is reserved by the chart and cannot be overridden." $key) }}
{{- end }}
{{- end }}

{{- /* Never allow a proxy or remote-policer credential to alias a Jenkins
       credential; doing so leaks the Jenkins API token outside the server. */ -}}
{{- if .Values.minibridge.enabled }}
{{- $usernameName := include "jenkins-mcp-server.usernameSecretName" . -}}
{{- $usernameKey := include "jenkins-mcp-server.usernameSecretKey" . -}}
{{- $tokenName := include "jenkins-mcp-server.tokenSecretName" . -}}
{{- $tokenKey := include "jenkins-mcp-server.tokenSecretKey" . -}}
{{- if .Values.minibridge.basicAuth.enabled }}
{{- $name := .Values.minibridge.basicAuth.existingSecret -}}
{{- $key := .Values.minibridge.basicAuth.secretKey -}}
{{- if or (and (eq $name $usernameName) (eq $key $usernameKey)) (and (eq $name $tokenName) (eq $key $tokenKey)) }}
{{- fail "minibridge.basicAuth must not reuse the Jenkins user ID or API token Secret key. Store the client authentication secret under a distinct key, preferably in its own Secret." }}
{{- end }}
{{- end }}
{{- if .Values.minibridge.policer.http.token.existingSecret }}
{{- $name := .Values.minibridge.policer.http.token.existingSecret -}}
{{- $key := .Values.minibridge.policer.http.token.secretKey -}}
{{- if or (and (eq $name $usernameName) (eq $key $usernameKey)) (and (eq $name $tokenName) (eq $key $tokenKey)) }}
{{- fail "minibridge.policer.http.token must not reuse the Jenkins user ID or API token Secret key." }}
{{- end }}
{{- end }}
{{- end }}
{{- if and .Values.minibridge.enabled .Values.minibridge.basicAuth.enabled }}
{{- if .Values.jenkins.credentials.externalSecret.enabled }}
{{- $credName := printf "%s-credentials" (include "jenkins-mcp-server.fullname" .) }}
{{- if eq .Values.minibridge.basicAuth.existingSecret $credName }}
{{- $key := .Values.minibridge.basicAuth.secretKey }}
{{- $provided := list .Values.jenkins.credentials.externalSecret.targetUsernameKey .Values.jenkins.credentials.externalSecret.targetTokenKey }}
{{- range .Values.jenkins.credentials.externalSecret.extraData }}{{- $provided = append $provided .secretKey }}{{- end }}
{{- if and (not .Values.jenkins.credentials.externalSecret.dataFrom) (not (has $key $provided)) }}
{{- fail (printf "minibridge.basicAuth points at the ExternalSecret target %q for key %q, but the ExternalSecret only creates keys %v. The pod would fail with CreateContainerConfigError. Add the key via externalSecret.extraData, or point basicAuth.existingSecret at a different Secret." $credName $key $provided) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}
