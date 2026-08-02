from pathlib import Path

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
