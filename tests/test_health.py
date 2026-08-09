import time
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


def test_probe_reports_an_unwritable_audit_file(tmp_path: Path) -> None:
    """probe() must detect the problem at startup, before any action runs.

    Whether that failure should also stop the pod serving traffic is a separate
    decision, covered by the readiness tests below.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    audit = AuditLogger(blocker / "audit.jsonl")
    audit.probe()

    assert audit.healthy is False
    _, payload = readiness(settings(), audit)
    assert payload["checks"]["audit_log_writable"] is False


def _broken_audit(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    audit = AuditLogger(blocker / "nested" / "audit.log")
    audit.emit("build.trigger", "success", status=201)
    return audit


def test_unwritable_audit_file_does_not_take_the_pod_out_of_service(tmp_path) -> None:
    """A failed redundant copy should not end the pod's ability to serve.

    Records also reach the process logs, which is the durable path in a
    cluster. Failing readiness here removes every replica from the Service at
    once, because a shared PVC is one volume and identically sized emptyDirs
    can fail together under similar load.
    """
    audit = _broken_audit(tmp_path)
    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="u",
        JENKINS_TOKEN="t",
        MCP_AUDIT_LOG_PATH=audit.path,
    )
    ready, payload = readiness(settings, audit)
    assert ready is True
    # The failure must still be visible to whoever looks.
    assert payload["checks"]["audit_log_writable"] is False
    assert payload["checks"]["audit_log_error"] == "NotADirectoryError"
    assert str(tmp_path) not in str(payload)


def test_audit_can_be_made_required_for_readiness(tmp_path) -> None:
    """Where the file is the record of account, the operator can demand it."""
    audit = _broken_audit(tmp_path)
    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="u",
        JENKINS_TOKEN="t",
        MCP_AUDIT_LOG_PATH=audit.path,
        MCP_AUDIT_REQUIRED_FOR_READINESS=True,
    )
    ready, payload = readiness(settings, audit)
    assert ready is False
    assert payload["status"] == "not_ready"


def test_a_healthy_audit_file_is_reported_and_stays_ready(tmp_path) -> None:
    audit = AuditLogger(tmp_path / "audit.jsonl")
    audit.emit("build.trigger", "success", status=201)
    for required in (False, True):
        settings = Settings(
            JENKINS_URL="https://jenkins.test",
            JENKINS_USERNAME="u",
            JENKINS_TOKEN="t",
            MCP_AUDIT_LOG_PATH=audit.path,
            MCP_AUDIT_REQUIRED_FOR_READINESS=required,
        )
        ready, payload = readiness(settings, audit)
        assert ready is True
        assert payload["checks"]["audit_log_writable"] is True


def test_readiness_reprobes_and_recovers_an_idle_audit_file(tmp_path) -> None:
    audit = _broken_audit(tmp_path)
    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="u",
        JENKINS_TOKEN="t",
        MCP_AUDIT_LOG_PATH=audit.path,
        MCP_AUDIT_REQUIRED_FOR_READINESS=True,
    )
    assert readiness(settings, audit)[0] is False

    blocker = tmp_path / "file"
    blocker.unlink()
    deadline = time.monotonic() + 1
    while not audit.healthy and time.monotonic() < deadline:
        readiness(settings, audit)
        time.sleep(0.01)
    ready, payload = readiness(settings, audit)
    assert ready is True
    assert payload["checks"]["audit_log_writable"] is True
    assert "audit_log_error" not in payload["checks"]


def test_optional_audit_reprobe_never_blocks_readiness(tmp_path) -> None:
    audit = _broken_audit(tmp_path)
    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="u",
        JENKINS_TOKEN="t",
        MCP_AUDIT_LOG_PATH=audit.path,
    )
    audit._io_lock.acquire()
    try:
        started = time.monotonic()
        ready, payload = readiness(settings, audit)
        elapsed = time.monotonic() - started
    finally:
        audit._io_lock.release()

    assert ready is True
    assert payload["checks"]["audit_log_writable"] is False
    assert elapsed < 0.1


def test_missing_jenkins_configuration_still_fails_readiness() -> None:
    """The change must not weaken the checks that gate a working server."""
    settings = Settings(
        JENKINS_URL="https://jenkins.test",
        JENKINS_USERNAME="",
        JENKINS_TOKEN="t",
    )
    ready, _ = readiness(settings)
    assert ready is False
