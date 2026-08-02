{{/*
Cross-field validation that would otherwise only surface at runtime.
*/}}
{{- define "jenkins-mcp-server.validate" -}}
{{- if and .Values.minibridge.enabled .Values.minibridge.basicAuth.enabled }}
{{- $credName := include "jenkins-mcp-server.credentialsSecretName" . }}
{{- if and .Values.externalSecret.enabled (eq .Values.minibridge.basicAuth.existingSecret $credName) }}
{{- $key := .Values.minibridge.basicAuth.secretKey }}
{{- $provided := list .Values.jenkins.credentials.usernameKey .Values.jenkins.credentials.tokenKey }}
{{- range .Values.externalSecret.extraData }}{{- $provided = append $provided .secretKey }}{{- end }}
{{- if and (not .Values.externalSecret.dataFrom) (not (has $key $provided)) }}
{{- fail (printf "minibridge.basicAuth points at the ExternalSecret target %q for key %q, but the ExternalSecret only creates keys %v. The pod would fail with CreateContainerConfigError. Add the key via externalSecret.extraData, or point basicAuth.existingSecret at a different Secret." $credName $key $provided) }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}
