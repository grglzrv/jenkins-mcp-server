# Guardrail policy for the Jenkins MCP server, evaluated by minibridge's Rego
# policer on every MCP request and response.
#
# Enable guardrails with:
#   REGO_POLICY_RUNTIME_GUARDRAILS="covert-instruction-detection schema-misuse-prevention \
#     secrets-redaction cross-origin-tool-access sensitive-pattern-detection \
#     shadowing-pattern-detection jenkins-destructive-tool-block"
#
# Optional shared-secret auth on the Authorization header:
#   REGO_POLICY_RUNTIME_BASIC_AUTH_SECRET
#
# Runtime env is exposed to Rego without the REGO_POLICY_RUNTIME_ prefix.

package main

import rego.v1

_covert_patterns := [
	"(?i)do not (tell|inform|alert|notify|mention)(?!\\s+(to\\s+)?use)",
	"(?i)hide this",
	"(?i)not visible",
	"(?i)user (should not|must not|cannot) see",
	"(?i)do(n't| not) (show|display|reveal|share)",
	"(?i)no need to mention this",
	"(?i)never (disclose|tell|show|reveal|leak)",
	"(?i)</?instructions>",
	"(?i)</?important>",
	"(?i)</?secret>",
	"(?i)</?system>",
	"(?i)very important",
]

_schema_keys := {
	"note", "notes", "feedback", "details", "extra", "additional",
	"metadata", "debug", "sidenote", "context", "annotation",
	"reasoning", "remark",
}

# Jenkins-flavoured secret shapes on top of the generic cloud ones. Jenkins API
# tokens are 34 hex chars; crumbs and cookies also leak through console output.
_redaction_patterns := [
	`(?i)jenkins[_-]?(api[_-]?)?token['"\s:=]+([0-9a-f]{32,34})`,
	`(?i)JENKINS_TOKEN['"\s:=]+([^\s'"]{8,})`,
	# Jenkins API tokens most often leak through console output as basic-auth
	# credentials: `curl -u user:<token>` or `https://user:<token>@host`.
	`(?i)-u\s+[\w.@+-]+:([0-9a-f]{30,64})`,
	`(?i)https?://[\w.@+-]+:([^\s/@'"]{12,})@`,
	`(?i)(Jenkins-Crumb)['"\s:=]+([0-9a-f]{32,})`,
	`(?i)JSESSIONID[.\w]*=([0-9a-f]{16,})`,
	`(gh[usop]_[A-Za-z0-9]{36})`,
	`(github_pat_\w{82})`,
	`(dckr_pat_[A-Za-z0-9_-]{27})`,
	`(AIza[\w-]{35})`,
	`((?:A3T|AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16})`,
	`(?i)aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*['"]?([A-Za-z0-9+/=]{40})['"]?`,
	`(ey[0-9A-Za-z_-]{17,}\.ey[0-9A-Za-z\\/\\_-]{17,}\.[0-9A-Za-z\\/\\_-]{10,}={0,2})`,
	`(xox[abrp]-\d{10,13}-\d{10,13}[A-Za-z0-9-]*)`,
	`-----BEGIN ([A-Z ]+)PRIVATE KEY-----`,
]

# Jenkins-specific sensitive surfaces: the script console is remote code
# execution, and credentials/config endpoints leak secrets.
_sensitive_patterns := [
	"(?i)/scriptText\\b",
	"(?i)/script\\b",
	"(?i)/credentials/store",
	"(?i)\\$JENKINS_HOME\\b",
	"(?i)/var/jenkins_home\\b",
	"(?i)secrets/master\\.key",
	"(?i)secrets/hudson\\.util\\.Secret",
	"\\.env\\b",
	"/etc/passwd\\b",
	"\\.ssh(/|$$)",
	"id_(rsa|ecdsa)\\b",
	"\\.\\./",
	`http:\/\/169\.254\.169\.254\/latest\/meta-data\/iam\/security-credentials(?:\/[\w\-]+)?`,
	`http:\/\/169\.254\.169\.254\/computeMetadata\/v1\/instance\/service-accounts\/(?:default|[\w\-]+)\/token`,
	`http:\/\/169\.254\.169\.254\/metadata\/identity\/oauth2\/token\?[^ ]*`,
]

_shadowing_patterns := [
	"(?i)instead of using",
	"(?i)before using",
	"(?i)after using",
	"(?i)ignore (previous|all|other) (instructions|directives)",
	"(?i)instead (of|do|provide|you should)",
]

_cross_tool_patterns := [
	`(?i)\b(?:use|run|launch|execute|start|invoke|trigger|initiate)\s+(?:the\s+)?(?:tool\s+)?([A-Za-z][A-Za-z0-9_-]{5,})\b`,
	`(?i)\b(?:when|after|before|upon)\s+(?:calling|running|invoking|executing)\s+([A-Za-z][A-Za-z0-9_-]{5,})\b`,
	`(?i)\b([A-Za-z][A-Za-z0-9_-]{5,})\.([A-Za-z][A-Za-z0-9_-]*)\s+should\s+(?:use|run|launch|execute|start|invoke|trigger|initiate)\b`,
]

# This server's own tools, plus common words that trip the heuristics.
_cross_tool_exclude := [
	"list_jobs",
	"get_job",
	"get_job_config",
	"create_job_from_xml",
	"update_job_config",
	"delete_job",
	"copy_job",
	"enable_job",
	"disable_job",
	"create_pipeline_job",
	"create_multibranch_pipeline",
	"scan_multibranch_pipeline",
	"trigger_build",
	"stop_build",
	"get_build_info",
	"get_build_console",
	"list_running_builds",
	"get_queue",
	"cancel_queue_item",
	"list_nodes",
	"get_node",
	"set_node_offline",
	"jenkins_admin_request",
	"to",
	"this",
	"that",
	"it",
	"something",
	"anything",
	"tool",
	"script",
	"function",
	"i",
	"you",
	"me",
	"we",
	"they",
	"them",
	"our",
	"us",
	"please",
	"today",
	"tomorrow",
	"yesterday",
	"warning",
	"discussion",
	"order",
	"case",
	"build",
	"builds",
	"jenkins",
	"pipeline",
	"branch",
	"master",
	"agent",
	"node",
	"nodes",
	"console",
	"queue",
	"job",
	"jobs",
	"status",
	"result",
	"project",
]

# Tool groups covering every tool this server exposes. Groups are referenced
# from the deny/allow lists with an @ prefix, e.g. TOOLS_DENY="@destructive".
_tool_groups := {
	"@read": {
		"list_jobs", "get_job", "get_job_config", "get_build_info",
		"get_build_console", "list_running_builds", "get_queue",
		"list_nodes", "get_node",
	},
	"@write": {
		"create_job_from_xml", "copy_job", "enable_job", "disable_job",
		"create_pipeline_job", "create_multibranch_pipeline",
		"scan_multibranch_pipeline", "trigger_build",
	},
	"@destructive": {
		"update_job_config", "delete_job", "stop_build",
		"cancel_queue_item", "set_node_offline",
	},
	"@admin": {"jenkins_admin_request"},
}

_all_tools := union({group | some _, group in _tool_groups})

# Expand a configured entry into concrete tool names: "@group" expands, a bare
# name is taken literally, and "@all" means every tool.
_expand(entry) := out if {
	entry == "@all"
	out := _all_tools
} else := out if {
	out := _tool_groups[entry]
} else := {entry}

_split_env(raw) := {t |
	some item in split(raw, " ")
	t := trim_space(item)
	t != ""
}

_denied_tools := union({expanded |
	some entry in _split_env(object.get(env, "TOOLS_DENY", ""))
	expanded := _expand(entry)
})

_allow_entries := _split_env(object.get(env, "TOOLS_ALLOW", ""))

_allowed_tools := union({expanded |
	some entry in _allow_entries
	expanded := _expand(entry)
})

# Default posture: every tool is allowed. An allow list is only enforced when
# one is actually configured; a deny entry always wins over an allow entry.
_tool_denied(name) if {
	name in _denied_tools
}

_tool_denied(name) if {
	count(_allow_entries) > 0
	not name in _allowed_tools
}

# Capability/method level: deny whole MCP methods, e.g. "resources/read".
_denied_methods := _split_env(object.get(env, "METHODS_DENY", ""))

env := opa.runtime().env

active_guardrails contains norm if {
	some raw in split(env.GUARDRAILS, " ")
	norm = lower(raw)
}

## Shared-secret authentication
#
reasons contains "invalid credentials" if {
	env.BASIC_AUTH_SECRET != ""
	input.agent.password != env.BASIC_AUTH_SECRET
}

## Tool and capability gating
#
# Configured through TOOLS_DENY / TOOLS_ALLOW / METHODS_DENY. With none set,
# every tool and capability is allowed, which is the chart default.

reasons contains msg if {
	input.mcp.method == "tools/call"
	_tool_denied(input.mcp.params.name)
	msg = sprintf("tool %v is not permitted by policy", [input.mcp.params.name])
}

reasons contains msg if {
	some method in _denied_methods
	input.mcp.method == method
	msg = sprintf("capability %v is not permitted by policy", [method])
}

## tools/list response checks
#
reasons contains msg if {
	"covert-instruction-detection" in active_guardrails
	some tool in input.mcp.result.tools
	some pattern in _covert_patterns
	regex.match(pattern, tool.description)
	msg = sprintf("covert instruction in tool %v: %v", [tool.name, pattern])
}

reasons contains msg if {
	"schema-misuse-prevention" in active_guardrails
	some tool in input.mcp.result.tools
	some prop, _ in tool.inputSchema.properties
	lower(prop) in _schema_keys
	msg = sprintf("schema parameter misuse in tool %v: %v", [tool.name, prop])
}

reasons contains msg if {
	"sensitive-pattern-detection" in active_guardrails
	some tool in input.mcp.result.tools
	some pattern in _sensitive_patterns
	regex.match(pattern, tool.description)
	msg = sprintf("sensitive resource in tool %v: %v", [tool.name, pattern])
}

reasons contains msg if {
	"shadowing-pattern-detection" in active_guardrails
	some tool in input.mcp.result.tools
	some pattern in _shadowing_patterns
	regex.match(pattern, tool.description)
	msg = sprintf("tool-shadowing in tool %v: %v", [tool.name, pattern])
}

reasons contains msg if {
	"cross-origin-tool-access" in active_guardrails
	some tool in input.mcp.result.tools
	some pattern in _cross_tool_patterns
	some tool_match in regex.find_all_string_submatch_n(pattern, tool.description, -1)
	extracted_tool := tool_match[count(tool_match) - 1]
	not extracted_tool in _cross_tool_exclude
	msg := sprintf("untrusted tool use detected in tool description %v: %v", [tool.name, extracted_tool])
}

## tools/call request checks
#
reasons contains msg if {
	"schema-misuse-prevention" in active_guardrails
	input.mcp.method == "tools/call"
	some arg_name, _ in input.mcp.params.arguments
	lower(arg_name) in _schema_keys
	msg = sprintf("schema parameter misuse in call args: %v", [arg_name])
}

reasons contains msg if {
	"sensitive-pattern-detection" in active_guardrails
	input.mcp.method == "tools/call"
	some pattern in _sensitive_patterns
	regex.match(pattern, sprintf("%v", [input.mcp.params.arguments]))
	msg = sprintf("sensitive content in call args: %v", [pattern])
}

## tools/call response checks
#
reasons contains msg if {
	"covert-instruction-detection" in active_guardrails
	some element in input.mcp.result.content
	element.type == "text"
	some pattern in _covert_patterns
	regex.match(pattern, sprintf("%v", [element.text]))
	msg = sprintf("covert content in call response: %v", [pattern])
}

reasons contains msg if {
	"shadowing-pattern-detection" in active_guardrails
	some element in input.mcp.result.content
	element.type == "text"
	some pattern in _shadowing_patterns
	regex.match(pattern, sprintf("%v", [element.text]))
	msg = sprintf("tool-shadowing in call response: %v", [pattern])
}

reasons contains msg if {
	"cross-origin-tool-access" in active_guardrails
	some element in input.mcp.result.content
	element.type == "text"
	some pattern in _cross_tool_patterns
	some tool_match in regex.find_all_string_submatch_n(pattern, element.text, -1)
	extracted_tool := tool_match[count(tool_match) - 1]
	not extracted_tool in _cross_tool_exclude
	msg := sprintf("untrusted tool detected in call response: %v", [extracted_tool])
}

## Response rewriting: hide denied tools from tools/list, then redact secrets.
#
# Both transformations produce JSON patches that are applied together, so a
# single `mcp` document is returned to minibridge.

_visible_tools := [tool |
	some tool in input.mcp.result.tools
	not _tool_denied(tool.name)
]

# Only patch when the listing actually changes, so unaffected responses are
# passed through untouched.
_tool_patches := [{
	"op": "replace",
	"path": "/result/tools",
	"value": _visible_tools,
}] if {
	count(input.mcp.result.tools) != count(_visible_tools)
} else := []

_redaction_patches := [patch |
	"secrets-redaction" in active_guardrails
	some idx, element in input.mcp.result.content
	element.type == "text"
	redactions := {m[count(m) - 1] |
		some pat in _redaction_patterns
		some m in regex.find_all_string_submatch_n(pat, element.text, -1)
	}
	repl_map := {c: "[REDACTED]" | c := redactions[_]}
	new_text := strings.replace_n(repl_map, element.text)
	new_text != element.text
	patch := {
		"op": "replace",
		"path": sprintf("/result/content/%d/text", [idx]),
		"value": new_text,
	}
]

mcp := json.patch(input.mcp, patches) if {
	patches := array.concat(_tool_patches, _redaction_patches)
	count(patches) > 0
}

allow if {
	count(reasons) == 0
}
