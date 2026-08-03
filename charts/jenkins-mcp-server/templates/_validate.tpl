{{/*
Cross-field validation that would otherwise only surface at runtime.
*/}}
{{- define "jenkins-mcp-server.validate" -}}
{{- /* Credential sources are mutually exclusive. Two of the three combinations
       previously resolved silently, so an operator could set existingSecret and
       have the chart quietly read from somewhere else. */ -}}
{{- $creds := .Values.jenkins.credentials -}}
{{- if and .Values.externalSecret.enabled $creds.create }}
{{- fail "externalSecret.enabled and jenkins.credentials.create are mutually exclusive: both produce a Secret named <fullname>-credentials and External Secrets would fight Helm for ownership. Pick one." }}
{{- end }}
{{- if and .Values.externalSecret.enabled $creds.existingSecret }}
{{- fail (printf "externalSecret.enabled is true but jenkins.credentials.existingSecret is set to %q. The ExternalSecret creates its own Secret, so existingSecret would be ignored. Clear it (existingSecret: \"\") to use External Secrets, or disable externalSecret to use your own Secret." $creds.existingSecret) }}
{{- end }}
{{- if and $creds.create $creds.existingSecret }}
{{- fail (printf "jenkins.credentials.create is true but existingSecret is set to %q. The chart would create a Secret and then read from a different one. Clear existingSecret to have the chart create it, or set create: false to use yours." $creds.existingSecret) }}
{{- end }}
{{- /* PodDisruptionBudget and autoscaling must agree, or a voluntary
       disruption can never proceed at minimum scale. */ -}}
{{- if and .Values.autoscaling.enabled .Values.podDisruptionBudget.enabled }}
{{- $min := .Values.podDisruptionBudget.minAvailable -}}
{{- /* --set yields float64, values.yaml yields int, and a percentage such as
       "50%" is a string that cannot be compared numerically. */ -}}
{{- if and $min (not (kindIs "string" $min)) }}
{{- if ge (int $min) (int .Values.autoscaling.minReplicas) }}
{{- fail (printf "podDisruptionBudget.minAvailable (%v) must be lower than autoscaling.minReplicas (%v), otherwise no pod can ever be evicted when scaled to the minimum." $min .Values.autoscaling.minReplicas) }}
{{- end }}
{{- end }}
{{- end }}
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
