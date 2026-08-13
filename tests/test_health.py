import json
import logging
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import httpx

from jenkins_mcp_server.audit import AuditLogger
from jenkins_mcp_server.config import Settings
from jenkins_mcp_server.diagnostics import JenkinsContact
from jenkins_mcp_server.health import (
    BoundedHealthServer,
    HealthHandler,
    readiness,
    start_health_server,
)


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


def test_health_responses_are_json_and_never_cached() -> None:
    contact = JenkinsContact()
    contact.record_failure(httpx.ConnectError("refused"))
    server = start_health_server(
        settings(MCP_HEALTH_HOST="127.0.0.1", MCP_HEALTH_PORT=0),
        contact=contact,
    )
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/readyz", timeout=1) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/json; charset=utf-8"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            payload = json.load(response)
            assert payload["jenkins"] == {
                "last_contact_age_seconds": None,
                "last_transport_error": "ConnectError",
            }
    finally:
        server.shutdown()
        server.server_close()


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


def _settings(**overrides):
    values = {
        "JENKINS_URL": "https://jenkins.test",
        "JENKINS_USERNAME": "u",
        "JENKINS_TOKEN": "t",
    }
    values.update(overrides)
    return Settings(**values)


def test_jenkins_reachability_is_reported_but_never_gates_readiness() -> None:
    """Readiness controls Service endpoints.

    Taking every replica out because Jenkins is restarting turns one upstream
    outage into two, and leaves callers with a refused connection instead of an
    error naming the cause.
    """
    contact = JenkinsContact()
    contact.record_failure(httpx.ConnectError("refused"))
    ready, payload = readiness(_settings(), contact=contact)
    assert ready is True
    assert payload["jenkins"]["last_transport_error"] == "ConnectError"


def test_reachability_is_null_until_the_pod_has_done_something() -> None:
    """Passive by design: no probe result is invented to fill the field."""
    fresh = JenkinsContact()
    assert fresh.snapshot() == {
        "last_contact_age_seconds": None,
        "last_transport_error": None,
    }


def test_any_http_response_counts_as_reaching_jenkins() -> None:
    """A 403 is a Jenkins problem, not a reachability problem."""
    contact = JenkinsContact()
    contact.record_failure(httpx.ConnectError("refused"))
    assert contact.snapshot()["last_transport_error"] == "ConnectError"
    contact.record_contact()
    snapshot = contact.snapshot()
    assert snapshot["last_transport_error"] is None
    assert snapshot["last_contact_age_seconds"] is not None


def test_transport_failure_warning_is_rate_limited_and_recovery_is_logged(caplog) -> None:
    contact = JenkinsContact(warning_interval_seconds=3600)
    with caplog.at_level(logging.INFO):
        contact.record_failure(httpx.ConnectError("first"))
        contact.record_failure(httpx.ConnectError("second"))
        contact.record_contact()
    assert caplog.text.count("Jenkins transport failure: ConnectError") == 1
    assert "Jenkins contact recovered after ConnectError" in caplog.text
    assert "first" not in caplog.text
    assert "second" not in caplog.text


def test_health_capacity_is_reserved_before_a_handler_thread_is_created(
    monkeypatch,
) -> None:
    """Assert at the thread-creation boundary without relying on TCP backlog timing."""
    server = BoundedHealthServer(
        ("127.0.0.1", 0),
        HealthHandler,
        max_connections=1,
    )
    created: list[object] = []
    refused: list[object] = []

    def create_handler_thread(self, request, client_address):
        created.append(request)
        # ThreadingHTTPServer.process_request is the method that creates the
        # handler thread. The only slot must already be held when it is called.
        assert self._slots.acquire(blocking=False) is False

    monkeypatch.setattr(ThreadingHTTPServer, "process_request", create_handler_thread)
    monkeypatch.setattr(server, "shutdown_request", refused.append)
    first = object()
    excess = object()
    try:
        server.process_request(first, ("127.0.0.1", 1))
        assert created == [first]

        # The fake base method deliberately does not start a thread, so the
        # first slot remains held. A second request must be closed without ever
        # reaching the thread-creation method.
        server.process_request(excess, ("127.0.0.1", 2))
        assert created == [first]
        assert refused == [excess]
    finally:
        server._slots.release()
        server.server_close()


def test_health_handler_timeout_is_bounded() -> None:
    assert HealthHandler.timeout == 5


def test_unexpected_health_handler_errors_are_not_suppressed(capsys) -> None:
    server = start_health_server(
        settings(MCP_HEALTH_HOST="127.0.0.1", MCP_HEALTH_PORT=0)
    )
    try:
        try:
            raise RuntimeError("unexpected health bug")
        except RuntimeError:
            server.handle_error(None, ("127.0.0.1", 1))
        assert "unexpected health bug" in capsys.readouterr().err
    finally:
        server.shutdown()
        server.server_close()


def test_connection_limit_warning_is_rate_limited(caplog, monkeypatch) -> None:
    server = BoundedHealthServer(
        ("127.0.0.1", 0),
        HealthHandler,
        max_connections=1,
    )
    refused: list[object] = []
    assert server._slots.acquire(blocking=False) is True
    monkeypatch.setattr(server, "shutdown_request", refused.append)
    caplog.set_level(logging.WARNING)
    try:
        requests = [object() for _ in range(20)]
        for request in requests:
            server.process_request(request, ("127.0.0.1", 1))
        messages = [
            record
            for record in caplog.records
            if "connection limit reached" in record.getMessage()
        ]
        assert len(messages) == 1
        assert refused == requests
    finally:
        server._slots.release()
        server.server_close()


def test_health_responses_do_not_advertise_a_server_banner() -> None:
    server = start_health_server(
        settings(MCP_HEALTH_HOST="127.0.0.1", MCP_HEALTH_PORT=0),
        None,
        JenkinsContact(),
    )
    host, port = server.server_address
    try:
        for method, path, expected in [
            ("GET", "/healthz", 200),
            ("GET", "/readyz", 200),
            ("GET", "/missing", 404),
            # Unsupported methods use BaseHTTPRequestHandler.send_error(), not
            # the JSON response helper. Cover that independent response path.
            ("POST", "/readyz", 501),
        ]:
            connection = HTTPConnection(host, port, timeout=2)
            connection.request(method, path, body=b"" if method == "POST" else None)
            response = connection.getresponse()
            body = response.read()
            assert response.status == expected
            assert response.getheader("Server") is None
            if path == "/healthz":
                assert body == b'{"status":"ok"}'
            connection.close()
    finally:
        server.shutdown()
        server.server_close()
