from __future__ import annotations

from xml.sax.saxutils import escape


def pipeline_job_xml(
    jenkinsfile: str,
    description: str = "Managed by Jenkins MCP",
) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<flow-definition plugin="workflow-job">
  <description>{escape(description)}</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition
    class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition"
    plugin="workflow-cps">
    <script>{escape(jenkinsfile)}</script>
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
    credentials_xml = (
        f"<credentialsId>{escape(credentials_id)}</credentialsId>"
        if credentials_id
        else ""
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject
  plugin="workflow-multibranch">
  <description>{escape(description)}</description>
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
          <remote>{escape(repo_url)}</remote>
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
    <scriptPath>{escape(script_path)}</scriptPath>
  </factory>
</org.jenkinsci.plugins.workflow.multibranch.WorkflowMultiBranchProject>'''
