from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_release_bump import (  # noqa: E402
    ReleaseBumpError,
    check_release_bump,
    compare_semver,
    is_release_path,
    parse_semver,
    require_newer_version,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/server.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "VERSION").write_text("1.20.0\n", encoding="utf-8")
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release-test@example.com")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    "path",
    [
        "src/server.py",
        "pyproject.toml",
        "Dockerfile",
        "docker/policy.rego",
        "charts/jenkins-mcp-server/templates/deployment.yaml",
        "charts/jenkins-mcp-server/values.schema.json",
        "compose.yaml",
        "deploy/kubernetes/base/deployment.yaml",
        "examples/argocd/application-oci.yaml",
        "examples/values/minibridge.yaml",
        "requirements/runtime.txt",
        "requirements/build.txt",
    ],
)
def test_release_path_policy_covers_deployable_inputs(path: str) -> None:
    assert is_release_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "charts/jenkins-mcp-server/README.md",
        "deploy/kubernetes/operator-notes.md",
        "docs/releasing/RELEASE.md",
        "tests/test_client.py",
        "integration/minibridge_probe.py",
        ".github/workflows/ci.yml",
        "docker/policy_test.rego",
    ],
)
def test_release_path_policy_exempts_non_runtime_inputs(path: str) -> None:
    assert not is_release_path(path)


def test_runtime_change_without_bump_fails(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    (repo / "src/server.py").write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(ReleaseBumpError, match="VERSION must change"):
        check_release_bump(base, repo)


def test_strictly_newer_version_passes(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    (repo / "src/server.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "VERSION").write_text("1.21.0\n", encoding="utf-8")

    assert check_release_bump(base, repo) == (
        "1.20.0",
        "1.21.0",
        ["src/server.py"],
    )


def test_lowering_the_version_is_allowed_locally(tmp_path: Path) -> None:
    """Withdrawing a tagged-then-pulled version is a legitimate correction.

    Ordering is enforced at publish time against the latest published release,
    which is what must never be republished. Requiring a local increase made a
    retraction impossible to express.
    """
    repo, base = _repository(tmp_path)
    (repo / "src/server.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "VERSION").write_text("1.19.0\n", encoding="utf-8")

    base_version, head_version, paths = check_release_bump(base, repo)
    assert (base_version, head_version) == ("1.20.0", "1.19.0")
    assert paths == ["src/server.py"]


def test_publish_time_check_still_refuses_a_lower_version() -> None:
    """The guarantee that matters: never republish over what is released."""
    with pytest.raises(ReleaseBumpError, match="must be newer"):
        require_newer_version("1.19.0", "1.20.0")


def test_documentation_only_change_needs_no_bump(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    chart = repo / "charts/jenkins-mcp-server"
    chart.mkdir(parents=True)
    (chart / "README.md").write_text("Documentation only.\n", encoding="utf-8")

    assert check_release_bump(base, repo) == ("", "", [])


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.20.1", "1.20.0", 1),
        ("1.21.0", "1.20.9", 1),
        ("2.0.0-rc.1", "2.0.0-beta.2", 1),
        ("2.0.0", "2.0.0-rc.1", 1),
        ("1.20.0", "1.20.0", 0),
        ("1.19.9", "1.20.0", -1),
    ],
)
def test_semver_precedence(left: str, right: str, expected: int) -> None:
    assert compare_semver(left, right) == expected


@pytest.mark.parametrize("value", ["1.2", "01.2.3", "1.2.3-01", "1.2.3-rc..1"])
def test_invalid_semver_is_rejected(value: str) -> None:
    with pytest.raises(ReleaseBumpError, match="invalid semantic version"):
        parse_semver(value)


def test_release_order_rejects_equal_or_older_versions() -> None:
    with pytest.raises(ReleaseBumpError, match="must be newer"):
        require_newer_version("1.20.0", "1.20.0")
    with pytest.raises(ReleaseBumpError, match="must be newer"):
        require_newer_version("1.19.9", "1.20.0")
    require_newer_version("1.21.0", "1.20.0")
