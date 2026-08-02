from jenkins_mcp_server.templates import multibranch_github_xml, pipeline_job_xml


def test_pipeline_xml_escapes() -> None:
    xml = pipeline_job_xml("echo '<x>'")
    assert "&lt;x&gt;" in xml
    assert "CpsFlowDefinition" in xml


def test_multibranch_xml() -> None:
    xml = multibranch_github_xml(
        "https://github.com/acme/repo.git",
        "git-creds",
    )
    assert "WorkflowMultiBranchProject" in xml
    assert "git-creds" in xml
    assert "repo.git" in xml
