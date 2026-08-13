from __future__ import annotations

import re
from urllib.parse import urlsplit
from xml.sax.saxutils import escape


def _xml_text(value: str, field: str) -> str:
    def valid_xml_10_character(character: str) -> bool:
        codepoint = ord(character)
        return (
            codepoint in {0x09, 0x0A, 0x0D}
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        )

    if not all(valid_xml_10_character(character) for character in value):
        raise ValueError(f"{field} contains a character XML 1.0 cannot represent")
    return escape(value)


def _repository_url(value: str) -> str:
    _xml_text(value, "repository_url")
    if not value or value != value.strip():
        raise ValueError("repository_url must not be empty or surrounded by whitespace")
    if any(
        character.isspace() or ord(character) <= 0x20 or ord(character) == 0x7F
        for character in value
    ):
        raise ValueError("repository_url must not contain whitespace or control characters")
    if "\\" in value:
        raise ValueError("repository_url must use canonical forward-slash URL separators")

    # Git's SCP-like SSH syntax has no URI scheme. Keep support for the common
    # user@host:path form, but reject arbitrary scheme-looking strings before
    # they reach Jenkins' Git plugin (notably file: and ext:: helpers).
    scp_like = re.fullmatch(
        r"[^@/:\s]+@[^@/:\s]+:[^\s]+",
        value,
    )
    if scp_like:
        return escape(value)

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("repository_url is not a valid Git repository URL") from exc

    allowed_schemes = {"http", "https", "ssh", "git", "git+ssh"}
    if parsed.scheme.casefold() not in allowed_schemes:
        raise ValueError(
            "repository_url must use http, https, ssh, git, git+ssh, "
            "or canonical user@host:path SSH syntax"
        )
    if parsed.query or parsed.fragment:
        raise ValueError(
            "repository_url must not contain a query string or fragment; "
            "use credentials_id for authentication"
        )
    if parsed.password is not None or (
        parsed.username is not None and parsed.scheme.lower() not in {"ssh", "git+ssh"}
    ):
        raise ValueError(
            "repository_url must not contain embedded credentials; "
            "use credentials_id for authentication"
        )
    if not hostname:
        raise ValueError("repository_url must include a host")
    if parsed.path in {"", "/"}:
        raise ValueError("repository_url must include a repository path")

    return escape(value)


def _script_path(value: str) -> str:
    _xml_text(value, "script_path")
    if not value or value != value.strip():
        raise ValueError("script_path must not be empty or surrounded by whitespace")
    windows_absolute = (
        len(value) >= 3
        and value[0].isalpha()
        and value[1] == ":"
        and value[2] in {"/", "\\"}
    )
    if value.startswith("/") or windows_absolute:
        raise ValueError("script_path must be repository-relative")
    if "\\" in value:
        raise ValueError("script_path must use canonical forward-slash separators")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(
            "script_path must be a canonical repository-relative path "
            "without empty, . or .. segments"
        )
    return escape(value)


def pipeline_job_xml(
    jenkinsfile: str,
    description: str = "Managed by Jenkins MCP",
) -> str:
    escaped_description = _xml_text(description, "description")
    escaped_jenkinsfile = _xml_text(jenkinsfile, "jenkinsfile")
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<flow-definition plugin="workflow-job">
  <description>{escaped_description}</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition
    class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition"
    plugin="workflow-cps">
    <script>{escaped_jenkinsfile}</script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>'''


def multibranch_github_xml(
    repo_url: str,
    credentials_id: str = "",
    script_path: str = "Jenkinsfile",
    description: str = "Managed by Jenkins MCP",
) -> str:
    escaped_repo_url = _repository_url(repo_url)
    escaped_script_path = _script_path(script_path)
    escaped_description = _xml_text(description, "description")
    credentials_xml = (
        f"<credentialsId>{_xml_text(credentials_id, 'credentials_id')}</credentialsId>"
        if credentials_id
        else ""
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject
  plugin="workflow-multibranch">
  <description>{escaped_description}</description>
  <properties/>
  <folderViews
    class="jenkins.branch.MultiBranchProjectViewHolder"
    plugin="branch-api">
    <owner
      class="org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject"
      reference="../.."/>
  </folderViews>
  <healthMetrics/>
  <icon class="jenkins.branch.MetadataActionFolderIcon" plugin="branch-api">
    <owner
      class="org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject"
      reference="../.."/>
  </icon>
  <orphanedItemStrategy
    class="com.cloudbees.hudson.plugins.folder.computed.DefaultOrphanedItemStrategy"
    plugin="cloudbees-folder">
    <pruneDeadBranches>true</pruneDeadBranches>
    <daysToKeep>-1</daysToKeep>
    <numToKeep>-1</numToKeep>
  </orphanedItemStrategy>
  <triggers>
    <com.cloudbees.hudson.plugins.folder.computed.PeriodicFolderTrigger
      plugin="cloudbees-folder">
      <spec>H H/4 * * *</spec>
      <interval>86400000</interval>
    </com.cloudbees.hudson.plugins.folder.computed.PeriodicFolderTrigger>
  </triggers>
  <sources class="jenkins.branch.MultiBranchProject$BranchSourceList" plugin="branch-api">
    <data>
      <jenkins.branch.BranchSource plugin="branch-api">
        <source class="jenkins.plugins.git.GitSCMSource" plugin="git">
          <id>mcp-git-source</id>
          <remote>{escaped_repo_url}</remote>
          {credentials_xml}
          <traits/>
        </source>
        <strategy class="jenkins.branch.DefaultBranchPropertyStrategy">
          <properties class="empty-list"/>
        </strategy>
      </jenkins.branch.BranchSource>
    </data>
    <owner
      class="org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject"
      reference="../.."/>
  </sources>
  <factory
    class="org.jenkinsci.plugins.workflow.multibranch.WorkflowBranchProjectFactory">
    <owner
      class="org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject"
      reference="../.."/>
    <scriptPath>{escaped_script_path}</scriptPath>
  </factory>
</org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject>'''
