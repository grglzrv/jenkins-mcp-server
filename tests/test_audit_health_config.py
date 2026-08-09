from __future__ import annotations

import json
import stat
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.health import start_health_server


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "JENKINS_URL": "https://jenkins.example/",
        "JENKINS_USERNAME": "hermes",
        "JENKINS_TOKEN": "secret",
        "MCP_PATH": "mcp/",
        "MCP_ALLOWED_JOBS": " AI/*, Platform/* ",
        "MCP_HEALTH_HOST": "127.0.0.1",
        "MCP_HEALTH_PORT": 0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_settings_normalization_and_ca(tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("certificate", encoding="utf-8")
    settings = make_settings(JENKINS_CA_BUNDLE=ca)
    assert settings.jenkins_url == "https://jenkins.example"
    assert settings.mount_path == "/mcp"
    assert settings.verify == str(ca)
    assert settings.job_patterns == ["AI/*", "Platform/*"]


def test_settings_boolean_verify_without_ca() -> None:
    settings = make_settings(JENKINS_VERIFY_TLS=False)
    assert settings.verify is False


@pytest.mark.parametrize(
    "url",
    [
        "jenkins.example",
        "ftp://jenkins.example",
        "https://u:p@jenkins.example",
        "https://jenkins.example/#x",
    ],
)
def test_settings_reject_unsafe_jenkins_urls(url: str) -> None:
    with pytest.raises(ValueError, match="JENKINS_URL"):
        make_settings(JENKINS_URL=url)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("JENKINS_TIMEOUT_SECONDS", 0),
        ("JENKINS_MAX_RETRIES", -1),
        ("JENKINS_MAX_RETRIES", 11),
        ("MCP_PORT", 65536),
        ("MCP_MAX_RESPONSE_BYTES", 1023),
        ("MCP_MAX_LOG_BYTES", 0),
    ],
)
def test_settings_reject_invalid_operational_limits(name: str, value: int) -> None:
    with pytest.raises(ValueError):
        make_settings(**{name: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {"MCP_AUDIT_REQUIRED_FOR_READINESS": True},
        {"MCP_AUDIT_MAX_BYTES": 1024},
        {"MCP_AUDIT_BACKUP_COUNT": 2},
        {"MCP_AUDIT_MAX_BYTES": 1024, "MCP_AUDIT_BACKUP_COUNT": 2},
        {
            "MCP_AUDIT_LOG_PATH": "/data/audit.jsonl",
            "MCP_AUDIT_MAX_BYTES": 1024,
        },
    ],
)
def test_settings_reject_incomplete_audit_configuration(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="AUDIT|audit"):
        make_settings(**overrides)


def test_settings_accept_complete_rotated_audit_configuration() -> None:
    configured = make_settings(
        MCP_AUDIT_LOG_PATH="/data/audit.jsonl",
        MCP_AUDIT_REQUIRED_FOR_READINESS=True,
        MCP_AUDIT_MAX_BYTES=1024,
        MCP_AUDIT_BACKUP_COUNT=2,
    )
    assert configured.audit_max_bytes == 1024
    assert configured.audit_backup_count == 2


def test_audit_file_output(tmp_path: Path) -> None:
    path = tmp_path / "audit" / "events.jsonl"
    AuditLogger(path).emit("job.create", "success", job="demo")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["action"] == "job.create"
    assert record["outcome"] == "success"
    assert record["job"] == "demo"
    assert record["ts"].endswith("+00:00")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_audit_identity_fields_cannot_be_overridden() -> None:
    record = json.loads(
        AuditLogger._line(
            "build.trigger",
            "success",
            {"ts": "forged", "status": 201},
        )
    )
    assert record["action"] == "build.trigger"
    assert record["outcome"] == "success"
    assert record["ts"] != "forged"


def test_audit_file_rotation_is_bounded_and_keeps_valid_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = AuditLogger(path, max_bytes=240, backup_count=2)
    audit.probe()
    assert path.read_bytes() == b""

    for number in range(12):
        audit.emit("build.trigger", "success", number=number)

    files = [path, path.with_name("audit.jsonl.1"), path.with_name("audit.jsonl.2")]
    assert all(candidate.is_file() for candidate in files)
    assert not path.with_name("audit.jsonl.3").exists()
    seen: list[int] = []
    for candidate in files:
        for line in candidate.read_text(encoding="utf-8").splitlines():
            seen.append(json.loads(line)["number"])
    assert 11 in seen
    assert len(seen) < 12


@pytest.mark.parametrize("values", [(-1, 1), (1, -1), (1, 0), (0, 1)])
def test_audit_logger_rejects_invalid_rotation_pairs(values: tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        AuditLogger(Path("audit.jsonl"), max_bytes=values[0], backup_count=values[1])


def test_audit_logger_rejects_rotation_without_a_path() -> None:
    with pytest.raises(ValueError, match="path"):
        AuditLogger(max_bytes=1024, backup_count=2)


def test_health_server_endpoints() -> None:
    server = start_health_server(make_settings())
    host, port = server.server_address
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}
        with urllib.request.urlopen(f"http://{host}:{port}/readyz", timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read())["status"] == "ready"
        try:
            urllib.request.urlopen(f"http://{host}:{port}/missing", timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("missing endpoint should return 404")
    finally:
        server.shutdown()
        server.server_close()
