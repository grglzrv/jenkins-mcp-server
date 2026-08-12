from __future__ import annotations

from pathlib import Path

import pytest

from jenkins_mcp_server.audit import AuditLogger, redact_query

# --- secrets must not enter the audit stream -------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/json?token=SECRET", "/api/json?token=[redacted]"),
        ("/api/json?TOKEN=SECRET", "/api/json?TOKEN=[redacted]"),
        ("/api/json?password=hunter2&depth=2", "/api/json?password=[redacted]&depth=2"),
        (
            "/api/json?access_token=a&refresh_token=b&tree=jobs",
            "/api/json?access_token=[redacted]&refresh_token=[redacted]&tree=jobs",
        ),
        # Values that locate rather than authenticate stay readable, or the
        # record loses the detail that makes it worth keeping.
        ("/api/json?tree=jobs[name]&depth=2", "/api/json?tree=jobs[name]&depth=2"),
        ("/job/AI/job/nightly/api/json", "/job/AI/job/nightly/api/json"),
    ],
)
def test_credential_query_values_are_redacted(path: str, expected: str) -> None:
    assert redact_query(path) == expected


def test_redaction_applies_to_every_record(tmp_path) -> None:
    """Applied in _line, so a new call site cannot forget it."""
    log = tmp_path / "audit.jsonl"
    audit = AuditLogger(log)
    audit.emit("admin.request", "success", path="/api/json?token=SECRET-VALUE")
    audit.emit("policy.denied", "denied", target="/api/json?password=SECRET-VALUE")

    text = log.read_text()
    assert "SECRET-VALUE" not in text
    assert text.count("[redacted]") == 2


def test_httpx_request_logging_is_quieted() -> None:
    """httpx logs the full URL at INFO, which can carry a query credential.

    jenkins_admin_request takes a caller-supplied path, so that line could put
    a secret in the process logs the audit stream is kept clean of.
    """
    main_module = (
        Path(__file__).resolve().parents[1] / "src/jenkins_mcp_server/__main__.py"
    ).read_text()
    assert 'logging.getLogger("httpx").setLevel(logging.WARNING)' in main_module
