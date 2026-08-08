{{- define "jenkins-mcp-server.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "jenkins-mcp-server.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "jenkins-mcp-server.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "jenkins-mcp-server.labels" -}}
helm.sh/chart: {{ include "jenkins-mcp-server.chart" . }}
{{ include "jenkins-mcp-server.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: jenkins-mcp-server
app.kubernetes.io/component: mcp-server
{{- end }}

{{- define "jenkins-mcp-server.selectorLabels" -}}
app.kubernetes.io/name: {{ include "jenkins-mcp-server.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "jenkins-mcp-server.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "jenkins-mcp-server.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Credential resolution. Exactly one source is enabled, enforced in _validate.tpl,
so each helper answers for whichever it is.
*/}}
{{- define "jenkins-mcp-server.credentialsSecretName" -}}
{{- $c := .Values.jenkins.credentials -}}
{{- if or $c.externalSecret.enabled $c.create.enabled -}}
{{- include "jenkins-mcp-server.fullname" . }}-credentials
{{- else -}}
{{- required "jenkins.credentials.existingSecret.name is required" $c.existingSecret.name -}}
{{- end -}}
{{- end }}

{{- define "jenkins-mcp-server.usernameSecretName" -}}
{{- $c := .Values.jenkins.credentials -}}
{{- if $c.secretKeyRefs.enabled -}}
{{- required "jenkins.credentials.secretKeyRefs.username.name is required" $c.secretKeyRefs.username.name -}}
{{- else -}}
{{- include "jenkins-mcp-server.credentialsSecretName" . -}}
{{- end -}}
{{- end }}

{{- define "jenkins-mcp-server.usernameSecretKey" -}}
{{- $c := .Values.jenkins.credentials -}}
{{- if $c.secretKeyRefs.enabled -}}
{{- required "jenkins.credentials.secretKeyRefs.username.key is required" $c.secretKeyRefs.username.key -}}
{{- else if $c.existingSecret.enabled -}}
{{- required "jenkins.credentials.existingSecret.usernameKey is required" $c.existingSecret.usernameKey -}}
{{- else if $c.create.enabled -}}
{{- $canonicalUsername := default "" (index $c.create "JENKINS_USERNAME") -}}
{{- $canonicalToken := default "" (index $c.create "JENKINS_TOKEN") -}}
{{- if or $canonicalUsername $canonicalToken -}}JENKINS_USERNAME{{- else -}}
{{- default "JENKINS_USERNAME" $c.create.usernameKey -}}
{{- end -}}
{{- else -}}
{{- required "jenkins.credentials.externalSecret.targetUsernameKey is required" $c.externalSecret.targetUsernameKey -}}
{{- end -}}
{{- end }}

{{- define "jenkins-mcp-server.tokenSecretName" -}}
{{- $c := .Values.jenkins.credentials -}}
{{- if $c.secretKeyRefs.enabled -}}
{{- required "jenkins.credentials.secretKeyRefs.token.name is required" $c.secretKeyRefs.token.name -}}
{{- else -}}
{{- include "jenkins-mcp-server.credentialsSecretName" . -}}
{{- end -}}
{{- end }}

{{- define "jenkins-mcp-server.tokenSecretKey" -}}
{{- $c := .Values.jenkins.credentials -}}
{{- if $c.secretKeyRefs.enabled -}}
{{- required "jenkins.credentials.secretKeyRefs.token.key is required" $c.secretKeyRefs.token.key -}}
{{- else if $c.existingSecret.enabled -}}
{{- required "jenkins.credentials.existingSecret.tokenKey is required" $c.existingSecret.tokenKey -}}
{{- else if $c.create.enabled -}}
{{- $canonicalUsername := default "" (index $c.create "JENKINS_USERNAME") -}}
{{- $canonicalToken := default "" (index $c.create "JENKINS_TOKEN") -}}
{{- if or $canonicalUsername $canonicalToken -}}JENKINS_TOKEN{{- else -}}
{{- default "JENKINS_TOKEN" $c.create.tokenKey -}}
{{- end -}}
{{- else -}}
{{- required "jenkins.credentials.externalSecret.targetTokenKey is required" $c.externalSecret.targetTokenKey -}}
{{- end -}}
{{- end }}

{{/* Image to run: the minibridge variant when the proxy is enabled. */}}
{{- define "jenkins-mcp-server.image" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- if .Values.minibridge.enabled -}}
{{- $repo := .Values.minibridge.image.repository | default .Values.image.repository -}}
{{- $mtag := .Values.minibridge.image.tag | default (printf "%s%s" $tag .Values.minibridge.image.tagSuffix) -}}
{{- printf "%s:%s" $repo $mtag -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}
{{- end -}}

{{/* Port serving MCP traffic: minibridge's listener when enabled. */}}
{{- define "jenkins-mcp-server.mcpPort" -}}
{{- if .Values.minibridge.enabled -}}{{ .Values.minibridge.port }}{{- else -}}{{ .Values.mcp.port }}{{- end -}}
{{- end -}}

{{/* Port serving health. minibridge exposes only "/" on its health listener. */}}
{{- define "jenkins-mcp-server.healthPort" -}}
{{- if .Values.minibridge.enabled -}}{{ .Values.minibridge.healthPort }}{{- else -}}{{ .Values.mcp.healthPort }}{{- end -}}
{{- end -}}

{{- define "jenkins-mcp-server.healthPath" -}}
{{- if .Values.minibridge.enabled -}}/{{- else -}}/healthz{{- end -}}
{{- end -}}

{{- define "jenkins-mcp-server.readyPath" -}}
{{- if .Values.minibridge.enabled -}}/{{- else -}}/readyz{{- end -}}
{{- end -}}

{{/*
minibridge environment. Variable names follow minibridge's own configuration;
every secret arrives via secretKeyRef so nothing sensitive lives in values.
*/}}
{{- define "jenkins-mcp-server.minibridgeEnv" -}}
{{- $mb := .Values.minibridge -}}
- name: MINIBRIDGE_MODE
  value: {{ eq $mb.mode "http" | ternary "aio" "backend" | quote }}
- name: MINIBRIDGE_LISTEN
  value: ":{{ $mb.port }}"
- name: MINIBRIDGE_HEALTH_LISTEN
  value: ":{{ $mb.healthPort }}"
- name: MINIBRIDGE_ENDPOINT_MCP
  value: {{ .Values.mcp.path | quote }}
{{- with $mb.log.level }}
- name: MINIBRIDGE_LOG_LEVEL
  value: {{ . | quote }}
{{- end }}
{{- with $mb.tracing.url }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ . | quote }}
{{- end }}
{{- if not $mb.sbom }}
- name: MINIBRIDGE_SBOM
  value: /sbom.disabled
{{- end }}
- name: MINIBRIDGE_MCP_USE_TEMPDIR
  value: {{ $mb.mcp.useTempDir | quote }}
{{- if $mb.tls.enabled }}
- name: MINIBRIDGE_TLS_SERVER_CERT
  value: /tls/{{ $mb.tls.certKey }}
- name: MINIBRIDGE_TLS_SERVER_KEY
  value: /tls/{{ $mb.tls.keyKey }}
{{- if or $mb.tls.passSecretKey $mb.tls.pass.valueFrom.name }}
- name: MINIBRIDGE_TLS_SERVER_KEY_PASS
  valueFrom:
    secretKeyRef:
      name: {{ default $mb.tls.existingSecret $mb.tls.pass.valueFrom.name }}
      key: {{ default $mb.tls.passSecretKey $mb.tls.pass.valueFrom.key }}
{{- end }}
{{- with $mb.tls.clientCASecretKey }}
- name: MINIBRIDGE_TLS_SERVER_CLIENT_CA
  value: /tls/{{ . }}
{{- end }}
{{- end }}
- name: MINIBRIDGE_POLICER_ENFORCE
  value: {{ $mb.policer.enforce | quote }}
{{- if $mb.policer.http.enabled }}
- name: MINIBRIDGE_POLICER_TYPE
  value: "http"
- name: MINIBRIDGE_POLICER_HTTP_URL
  value: {{ required "minibridge.policer.http.url is required when the http policer is enabled" $mb.policer.http.url | quote }}
{{- with $mb.policer.http.caPath }}
- name: MINIBRIDGE_POLICER_HTTP_CA
  value: {{ . | quote }}
{{- end }}
{{- with $mb.policer.http.token.existingSecret }}
- name: MINIBRIDGE_POLICER_HTTP_BEARER_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $mb.policer.http.token.secretKey }}
{{- end }}
{{- else if $mb.policer.rego.enabled }}
- name: MINIBRIDGE_POLICER_TYPE
  value: "rego"
- name: MINIBRIDGE_POLICER_REGO_POLICY
  value: {{ $mb.policer.rego.policy | quote }}
{{- end }}
- name: TOOLS_DENY
  value: {{ join " " $mb.tools.deny | quote }}
- name: TOOLS_ALLOW
  value: {{ join " " $mb.tools.allow | quote }}
- name: METHODS_DENY
  value: {{ join " " $mb.methodsDeny | quote }}
- name: GUARDRAILS
  value: {{ join " " $mb.guardrails | quote }}
{{- if $mb.basicAuth.enabled }}
- name: BASIC_AUTH_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ required "minibridge.basicAuth.existingSecret is required when basicAuth is enabled" $mb.basicAuth.existingSecret }}
      key: {{ $mb.basicAuth.secretKey }}
{{- end }}
{{- end -}}
