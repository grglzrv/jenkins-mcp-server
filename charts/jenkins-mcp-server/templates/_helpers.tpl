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

{{- define "jenkins-mcp-server.credentialsSecretName" -}}
{{- if .Values.externalSecret.enabled }}
{{- include "jenkins-mcp-server.fullname" . }}-credentials
{{- else if .Values.jenkins.credentials.create }}
{{- include "jenkins-mcp-server.fullname" . }}-credentials
{{- else }}
{{- required "jenkins.credentials.existingSecret is required unless credential creation or ExternalSecret is enabled" .Values.jenkins.credentials.existingSecret }}
{{- end }}
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
{{- if $mb.tls.enabled }}
- name: MINIBRIDGE_TLS_SERVER_CERT
  value: /tls/{{ $mb.tls.certKey }}
- name: MINIBRIDGE_TLS_SERVER_KEY
  value: /tls/{{ $mb.tls.keyKey }}
{{- with $mb.tls.passSecretKey }}
- name: MINIBRIDGE_TLS_SERVER_KEY_PASS
  valueFrom:
    secretKeyRef:
      name: {{ $mb.tls.existingSecret }}
      key: {{ . }}
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
- name: MINIBRIDGE_POLICER_URL
  value: {{ required "minibridge.policer.http.url is required when the http policer is enabled" $mb.policer.http.url | quote }}
{{- with $mb.policer.http.caPath }}
- name: MINIBRIDGE_POLICER_CA
  value: {{ . | quote }}
{{- end }}
{{- with $mb.policer.http.token.existingSecret }}
- name: MINIBRIDGE_POLICER_TOKEN
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
