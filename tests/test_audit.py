from __future__ import annotations

import asyncio
import hashlib
import json
import logging

import httpx
import pytest

from jenkins_mcp_server.__main__ import configure_logging
from jenkins_mcp_server.audit import (
    _MAX_FIELD_JSON_BYTES,
    _MAX_RECORD_BYTES,
    AuditLogger,
    QueryRedactingLogFilter,
    redact_query,
)

# --- secrets must not enter the audit stream -------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/json?token=SECRET", "/api/json?[redacted]"),
        ("/api/json?%74oken=SECRET", "/api/json?[redacted]"),
        ("/api/json?to%6ben=SECRET", "/api/json?[redacted]"),
        ("/api/json?auth.token=SECRET", "/api/json?[redacted]"),
        ("/api/json?custom_credential=SECRET", "/api/json?[redacted]"),
        ("/api/json?tree=jobs[name]&depth=2", "/api/json?[redacted]"),
        ("/job/AI/job/nightly/api/json", "/job/AI/job/nightly/api/json"),
    ],
)
def test_query_payload_is_redacted(path: str, expected: str) -> None:
    assert redact_query(path) == expected


def test_redaction_applies_to_every_record(tmp_path) -> None:
    """Applied in _line, so a new call site cannot forget it."""
    log = tmp_path / "audit.jsonl"
    audit = AuditLogger(log)
    audit.emit("admin.request", "success", path="/api/json?token=SECRET-VALUE")
    audit.emit(
        "policy.denied",
        "denied",
        context={
            "targets": [
                "/api/json?%74oken=SECRET-VALUE",
                "/api/json?custom_credential=SECRET-VALUE",
            ]
        },
    )

    text = log.read_text()
    assert "SECRET-VALUE" not in text
    assert text.count("[redacted]") == 3


def test_log_filter_redacts_httpx_url_object() -> None:
    """Exercise the actual LogRecord shape emitted by HTTPX."""
    marker = "HTTPX-QUERY-SECRET"
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        'HTTP Request: %s %s "%s %d %s"',
        (
            "GET",
            httpx.URL(f"https://jenkins.test/api/json?%74oken={marker}"),
            "HTTP/1.1",
            200,
            "OK",
        ),
        None,
    )

    assert QueryRedactingLogFilter().filter(record)
    rendered = record.getMessage()
    assert marker not in rendered
    assert "https://jenkins.test/api/json?[redacted]" in rendered


def test_configured_logging_preserves_httpx_info_without_query(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Run a real HTTPX request instead of inspecting configuration source.

    jenkins_admin_request takes a caller-supplied path, so that line could put
    a secret in the process logs the audit stream is kept clean of.
    """
    marker = "HTTPX-LIVE-QUERY-SECRET"
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    original_logger_filters = [list(logger.filters) for logger in loggers]
    root_handlers = list(logging.getLogger().handlers)
    original_handler_filters = [list(handler.filters) for handler in root_handlers]

    async def request() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ) as client:
            await client.get(f"https://jenkins.test/api/json?token={marker}")

    try:
        configure_logging(verbose=False)
        # A second call must not stack duplicate filters.
        configure_logging(verbose=False)
        caplog.set_level(logging.INFO)
        asyncio.run(request())
        assert marker not in caplog.text
        assert "HTTP Request" in caplog.text
        assert "https://jenkins.test/api/json?[redacted]" in caplog.text
        assert all(
            sum(isinstance(item, QueryRedactingLogFilter) for item in logger.filters)
            == 1
            for logger in loggers
        )
    finally:
        for logger, filters in zip(loggers, original_logger_filters, strict=True):
            logger.filters[:] = filters
        for handler, filters in zip(
            root_handlers, original_handler_filters, strict=True
        ):
            handler.filters[:] = filters


def test_audit_json_remains_valid_after_recursive_redaction(tmp_path) -> None:
    log = tmp_path / "audit.jsonl"
    AuditLogger(log).emit(
        "admin.request",
        "success",
        context={"path": "/api/json?token=SECRET", "status": 200},
    )
    record = json.loads(log.read_text())
    assert record["context"]["path"] == "/api/json?[redacted]"
    assert record["context"]["status"] == 200


def test_process_audit_json_remains_complete_under_root_filter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "PROCESS-AUDIT-QUERY-SECRET"
    handler = caplog.handler
    query_filter = QueryRedactingLogFilter()
    handler.addFilter(query_filter)
    try:
        with caplog.at_level(logging.INFO, logger="jenkins_mcp.audit"):
            AuditLogger().emit(
                "admin.request",
                "success",
                path=f"/api/json?%74oken={marker}",
                status=200,
            )
    finally:
        handler.removeFilter(query_filter)

    line = next(
        record.getMessage().removeprefix("AUDIT ")
        for record in caplog.records
        if record.name == "jenkins_mcp.audit"
    )
    assert marker not in line
    record = json.loads(line)
    assert record["path"] == "/api/json?[redacted]"
    assert record["status"] == 200


# --- record size is not chosen by the caller -------------------------------


def test_long_caller_supplied_fields_are_bounded(tmp_path) -> None:
    """Job and node names reach the record verbatim, so their length was the
    caller's choice. A refused call must still be recorded, but the identifying
    prefix is what makes the record useful; the rest is padding written to the
    audit file and to the log stream a SIEM ingests.
    """
    log = tmp_path / "audit.jsonl"
    audit = AuditLogger(log)
    audit.emit("policy.denied", "denied", target="B" * (2 * 1024 * 1024))

    record = json.loads(log.read_text())
    assert len(record["target"]) <= 1024
    assert record["target"].startswith("BBBB")
    assert "[truncated sha256=" in record["target"]
    assert hashlib.sha256(b"B" * (2 * 1024 * 1024)).hexdigest() in record["target"]
    assert "bytes=2097152]" in record["target"]
    assert log.stat().st_size < 4096


def test_bounding_reaches_nested_values(tmp_path) -> None:
    log = tmp_path / "audit.jsonl"
    AuditLogger(log).emit(
        "policy.denied", "denied", detail={"job": "B" * 5000, "items": ["C" * 5000]}
    )
    record = json.loads(log.read_text())
    assert len(record["detail"]["job"]) <= 1024
    assert len(record["detail"]["items"][0]) <= 1024


def test_bounding_covers_unicode_keys_and_tuples(tmp_path) -> None:
    """Character counts, mapping values, and lists are not a complete bound."""
    log = tmp_path / "audit.jsonl"
    long_key = "K" * (2 * 1024 * 1024)
    AuditLogger(log).emit(
        "policy.denied",
        "denied",
        unicode="😀" * 1024,
        detail={long_key: ("T" * (2 * 1024 * 1024),)},
    )

    raw = log.read_bytes()
    record = json.loads(raw)
    assert len(raw) <= _MAX_RECORD_BYTES + 1  # JSONL newline
    assert len(json.dumps(record["unicode"])[1:-1]) <= _MAX_FIELD_JSON_BYTES
    [bounded_key] = record["detail"]
    assert "sha256=" in bounded_key
    assert "sha256=" in record["detail"][bounded_key][0]


def test_many_individually_small_values_cannot_bypass_record_cap(tmp_path) -> None:
    """A per-field limit alone still allowed a multi-megabyte record."""
    log = tmp_path / "audit.jsonl"
    AuditLogger(log).emit(
        "policy.denied",
        "denied",
        target="AI/nightly",
        detail=["x" * 1024] * 10_000,
    )

    raw = log.read_bytes()
    record = json.loads(raw)
    assert len(raw) <= _MAX_RECORD_BYTES + 1
    assert record["target"] == "AI/nightly"
    assert record["audit_record_truncated"] is True
    assert record["audit_record_pre_cap_bytes"] > _MAX_RECORD_BYTES
    assert len(record["audit_record_sha256"]) == 64


def test_truncated_values_with_same_prefix_retain_distinct_evidence(tmp_path) -> None:
    log = tmp_path / "audit.jsonl"
    prefix = "same-prefix/" + "A" * 5000
    audit = AuditLogger(log)
    audit.emit("policy.denied", "denied", target=f"{prefix}/one")
    audit.emit("policy.denied", "denied", target=f"{prefix}/two")

    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert records[0]["target"] != records[1]["target"]
    assert all("sha256=" in record["target"] for record in records)


def test_file_and_process_sink_receive_the_same_bounded_record(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    log = tmp_path / "audit.jsonl"
    with caplog.at_level(logging.INFO, logger="jenkins_mcp.audit"):
        AuditLogger(log).emit(
            "policy.denied", "denied", target="Z" * (2 * 1024 * 1024)
        )

    file_line = log.read_text().strip()
    process_line = next(
        record.getMessage().removeprefix("AUDIT ")
        for record in caplog.records
        if record.name == "jenkins_mcp.audit"
    )
    assert file_line == process_line
    assert len(file_line.encode()) <= _MAX_RECORD_BYTES


def test_ordinary_records_are_untouched(tmp_path) -> None:
    """Bounding must not disturb the records operators actually read."""
    log = tmp_path / "audit.jsonl"
    path = "/job/AI/job/nightly/api/json"
    AuditLogger(log).emit("api.get", "success", path=path, status=200)

    record = json.loads(log.read_text())
    assert record["path"] == path
    assert record["status"] == 200
    assert "truncated" not in log.read_text()
