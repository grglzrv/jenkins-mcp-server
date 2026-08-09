from pathlib import Path

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.health import readiness


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "JENKINS_URL": "https://jenkins.example.ts.net",
        "JENKINS_USERNAME": "hermes",
        "JENKINS_TOKEN": "secret",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_readiness_without_custom_ca() -> None:
    ready, payload = readiness(settings())
    assert ready is True
    assert payload["status"] == "ready"


def test_readiness_fails_when_ca_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pem"
    ready, payload = readiness(settings(JENKINS_CA_BUNDLE=missing))
    assert ready is False
    assert payload["checks"]["jenkins_ca_bundle_exists"] is False


def test_readiness_degrades_when_configured_audit_file_is_unwritable(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    audit = AuditLogger(blocker / "audit.jsonl")
    audit.probe()

    ready, payload = readiness(settings(), audit)
    assert ready is False
    assert payload["checks"]["audit_log_writable"] is False
