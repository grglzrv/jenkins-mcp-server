from collections.abc import Callable

import pytest

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


@pytest.mark.parametrize(
    "repository_url",
    [
        "",
        " https://github.com/acme/repo.git",
        "https://token@github.com/acme/repo.git",
        "https://user:password@github.com/acme/repo.git",
        "ssh://git:password@github.com/acme/repo.git",
        "https://github.com/acme/repo.git?token=secret",
        "https://github.com/acme/repo.git#private-ref",
        "https://github.com/acme/repo.git\nignored",
        "https://github.com/acme/repo.git\x7fignored",
        "https://github.com\\acme/repo.git",
        "git://token@github.com/acme/repo.git",
        "file:///var/lib/jenkins/secrets/repository",
        "ftp://github.com/acme/repo.git",
        "ext::sh -c id",
        "https://github.com/",
        "https://github.com:not-a-port/acme/repo.git",
        "ssh://git@github.com:70000/acme/repo.git",
    ],
)
def test_multibranch_rejects_unsafe_repository_urls(repository_url: str) -> None:
    with pytest.raises(ValueError, match="repository_url"):
        multibranch_github_xml(repository_url)


@pytest.mark.parametrize(
    "repository_url",
    [
        "https://github.com/acme/repo.git",
        "ssh://git@github.com/acme/repo.git",
        "git+ssh://git@github.com/acme/repo.git",
        "git@github.com:acme/repo.git",
    ],
)
def test_multibranch_keeps_normal_git_url_forms(repository_url: str) -> None:
    xml = multibranch_github_xml(repository_url)
    assert repository_url in xml


@pytest.mark.parametrize(
    "script_path",
    [
        "",
        " Jenkinsfile",
        "/Jenkinsfile",
        "C:/Jenkinsfile",
        "../Jenkinsfile",
        "ci/../Jenkinsfile",
        "./Jenkinsfile",
        "ci//Jenkinsfile",
        "ci\\Jenkinsfile",
    ],
)
def test_multibranch_requires_canonical_repository_relative_script_path(
    script_path: str,
) -> None:
    with pytest.raises(ValueError, match="script_path"):
        multibranch_github_xml(
            "https://github.com/acme/repo.git",
            script_path=script_path,
        )


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (pipeline_job_xml, {"jenkinsfile": "echo 'ok'\x00"}),
        (
            pipeline_job_xml,
            {"jenkinsfile": "echo 'ok'", "description": "invalid\x1fdescription"},
        ),
        (
            multibranch_github_xml,
            {"repo_url": "https://github.com/acme/repo.git\x00"},
        ),
        (
            multibranch_github_xml,
            {
                "repo_url": "https://github.com/acme/repo.git",
                "credentials_id": "credential\ud800",
            },
        ),
    ],
)
def test_generated_xml_rejects_characters_xml_1_0_cannot_represent(
    factory: Callable[..., str],
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="XML 1.0"):
        factory(**kwargs)
