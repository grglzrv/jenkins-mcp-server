from __future__ import annotations

import pytest
from pydantic import ValidationError

from jenkins_mcp_server.config import Settings


def test_environment_variables_are_matched_case_sensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lowercase duplicate must not override a chart-validated value.

    pydantic-settings matches case-insensitively by default, so `jenkins_token`
    would be read as JENKINS_TOKEN. The chart rejects chart-owned names in
    mcp.extraEnv, but it can only reject the spellings it enumerates, so the
    server must not accept the others.
    """
    for name in ("JENKINS_URL", "JENKINS_USERNAME", "JENKINS_TOKEN"):
        monkeypatch.setenv(name, "from-secret")
    monkeypatch.setenv("JENKINS_URL", "https://jenkins.test")
    monkeypatch.setenv("jenkins_token", "injected")
    monkeypatch.setenv("mcp_read_only", "false")
    monkeypatch.setenv("MCP_READ_ONLY", "true")

    settings = Settings()
    assert settings.jenkins_token == "from-secret"
    # The uppercase spelling is still authoritative.
    assert settings.read_only is True


def test_uppercase_environment_variables_still_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case sensitivity must not break the documented spellings."""
    monkeypatch.setenv("JENKINS_URL", "https://jenkins.test")
    monkeypatch.setenv("JENKINS_USERNAME", "user")
    monkeypatch.setenv("JENKINS_TOKEN", "token")
    monkeypatch.setenv("MCP_ALLOWED_JOBS", "AI/*")
    monkeypatch.setenv("MCP_REDACT_PARAMETER_PATTERNS", "*_AUTH, SIGNING_*")
    monkeypatch.setenv("MCP_READ_ONLY", "true")
    monkeypatch.setenv("MCP_ALLOW_SCRIPT_CONSOLE", "true")

    settings = Settings()
    assert settings.job_patterns == ["AI/*"]
    assert settings.parameter_redaction_patterns == ["*_AUTH", "SIGNING_*"]
    assert settings.read_only is True
    assert settings.allow_script_console is True



def test_init_keyword_aliases_are_case_sensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("JENKINS_URL", "JENKINS_USERNAME", "JENKINS_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        Settings(
            Jenkins_URL="https://jenkins.test",
            Jenkins_USERNAME="user",
            Jenkins_TOKEN="token",
        )
