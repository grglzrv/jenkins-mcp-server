from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

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


def test_audit_file_output(tmp_path: Path) -> None:
    path = tmp_path / "audit" / "events.jsonl"
    AuditLogger(path).emit("job.create", "success", job="demo")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["action"] == "job.create"
    assert record["outcome"] == "success"
    assert record["job"] == "demo"
    assert record["ts"].endswith("+00:00")


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
