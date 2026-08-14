from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # POSIX containers use this to coordinate rotation across shared PVCs.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("jenkins_mcp.audit")

_REDACTED = "[redacted]"
# Match URL/path substrings inside already-rendered log messages without
# treating an unrelated question mark as a query delimiter. Quotes terminate
# the match so JSON audit lines remain complete and parseable.
_URL_QUERY_IN_TEXT = re.compile(
    r"(?P<path>(?:https?://|/)[^\s\"'?]*)\?[^\s\"']*",
    re.IGNORECASE,
)


# Audit records go to both the process log and the optional JSONL file. Bound
# the JSON representation, not Python character counts: one non-ASCII character
# can occupy twelve bytes after JSON escaping. The record ceiling is a second
# line of defence for containers containing many individually small values.
_MAX_FIELD_JSON_BYTES = 1024
_MAX_RECORD_BYTES = 16 * 1024
_MAX_RECORD_VALUES = 128
_MAX_RECORD_DEPTH = 8


def _json_char_bytes(character: str) -> int:
    """Return this character's size inside json.dumps' string payload."""
    codepoint = ord(character)
    if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
        return 2
    if codepoint < 0x20:
        return 6
    if codepoint < 0x80:
        return 1
    if codepoint <= 0xFFFF:
        return 6
    return 12


def _bound(value: str) -> str:
    """Bound one JSON string while retaining evidence about its full value."""
    json_bytes = 0
    utf8_bytes = 0
    digest = hashlib.sha256()
    for offset in range(0, len(value), 4096):
        chunk = value[offset : offset + 4096]
        encoded = chunk.encode("utf-8", errors="surrogatepass")
        digest.update(encoded)
        utf8_bytes += len(encoded)
        json_bytes += sum(_json_char_bytes(character) for character in chunk)
    if json_bytes <= _MAX_FIELD_JSON_BYTES:
        return value

    note = (
        "... [truncated "
        f"sha256={digest.hexdigest()} bytes={utf8_bytes}]"
    )
    keep_bytes = _MAX_FIELD_JSON_BYTES - len(note)
    kept: list[str] = []
    used = 0
    for character in value:
        width = _json_char_bytes(character)
        if used + width > keep_bytes:
            break
        kept.append(character)
        used += width
    return f"{''.join(kept)}{note}"


def redact_query(value: str) -> str:
    """Remove a URL query payload while retaining the path for diagnostics.

    ``jenkins_admin_request`` accepts arbitrary Jenkins paths and query names,
    so no finite list of credential keys can be complete. Encoded names such
    as ``%74oken`` also become ``token`` only after the naive redactor has run.
    Treat the entire query as untrusted and keep only the endpoint path.
    """
    head, separator, _ = value.partition("?")
    if not separator:
        return value
    return f"{head}?{_REDACTED}"


def _redact_queries_in_text(value: str) -> str:
    """Redact URL substrings without truncating the surrounding log message."""
    return _URL_QUERY_IN_TEXT.sub(
        lambda match: f"{match.group('path')}?{_REDACTED}", value
    )


def _bounded_fields(
    value: Any,
    remaining: list[int],
    depth: int = 0,
) -> Any:
    """Make audit metadata JSON-safe with bounded depth and cardinality."""
    if remaining[0] <= 0:
        return "[audit values omitted]"
    remaining[0] -= 1
    if depth >= _MAX_RECORD_DEPTH:
        return "[audit nesting omitted]"
    if isinstance(value, str):
        return _bound(redact_query(value))
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[non-finite number omitted]"
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        return (
            f"[binary sha256={hashlib.sha256(raw).hexdigest()} "
            f"bytes={len(raw)}]"
        )
    if isinstance(value, Mapping):
        bounded: dict[str, Any] = {}
        processed = 0
        for key, item in value.items():
            if remaining[0] <= 0:
                break
            bounded_key = _bound(redact_query(str(key)))
            bounded[bounded_key] = _bounded_fields(item, remaining, depth + 1)
            processed += 1
        omitted = max(len(value) - processed, 0)
        if omitted:
            bounded["_audit_items_omitted"] = omitted
        return bounded
    if isinstance(value, list | tuple | set | frozenset):
        bounded_items: list[Any] = []
        for item in value:
            if remaining[0] <= 0:
                break
            bounded_items.append(_bounded_fields(item, remaining, depth + 1))
        omitted = max(len(value) - len(bounded_items), 0)
        if omitted:
            bounded_items.append({"_audit_items_omitted": omitted})
        return bounded_items
    return _bound(redact_query(str(value)))


def _encode_record(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True)


def _cap_record(record: dict[str, Any]) -> str:
    """Apply a strict final ceiling while retaining event identity and proof."""
    line = _encode_record(record)
    encoded = line.encode("utf-8")
    if len(encoded) <= _MAX_RECORD_BYTES:
        return line

    summary: dict[str, Any] = {
        "ts": record["ts"],
        "action": record["action"],
        "outcome": record["outcome"],
        "audit_record_truncated": True,
        "audit_record_pre_cap_bytes": len(encoded),
        "audit_record_sha256": hashlib.sha256(encoded).hexdigest(),
    }
    omitted: list[str] = []
    identity = {"ts", "action", "outcome"}
    priority = (
        "check",
        "target",
        "reason",
        "job",
        "path",
        "status",
        "error",
        "category",
        "policy_action",
        "queue_item",
        "request_bytes",
        "request_limit_bytes",
    )
    keys = [key for key in priority if key in record]
    keys.extend(sorted(key for key in record if key not in identity | set(keys)))
    for key in keys:
        candidate = {**summary, key: record[key]}
        if len(_encode_record(candidate).encode("utf-8")) <= _MAX_RECORD_BYTES:
            summary[key] = record[key]
        else:
            omitted.append(key)
    if omitted:
        candidate = {**summary, "audit_fields_omitted": omitted}
        if len(_encode_record(candidate).encode("utf-8")) <= _MAX_RECORD_BYTES:
            summary = candidate
    capped = _encode_record(summary)
    if len(capped.encode("utf-8")) > _MAX_RECORD_BYTES:
        # The identity fields are themselves bounded, so this emergency shape
        # remains finite even if a future edit accidentally grows the summary.
        capped = _encode_record(
            {
                "ts": record["ts"],
                "action": record["action"],
                "outcome": record["outcome"],
                "audit_record_truncated": True,
                "audit_record_pre_cap_bytes": len(encoded),
                "audit_record_sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    return capped


class QueryRedactingLogFilter(logging.Filter):
    """Scrub URL queries from third-party structured logging arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX keeps the URL object in ``record.args``. Replace it before a
        # handler or structured-log exporter can retain the original object.
        # Scrub ``msg`` too so an already-rendered third-party record is safe.
        record.msg = self._scrub_log_value(record.msg)
        record.args = self._scrub_log_value(record.args)
        return True

    @staticmethod
    def _scrub_log_value(value: Any) -> Any:
        if isinstance(value, str):
            return _redact_queries_in_text(value)
        if isinstance(value, Mapping):
            return {
                key: QueryRedactingLogFilter._scrub_log_value(item)
                for key, item in value.items()
            }
        if isinstance(value, tuple):
            return tuple(
                QueryRedactingLogFilter._scrub_log_value(item) for item in value
            )
        if isinstance(value, list):
            return [
                QueryRedactingLogFilter._scrub_log_value(item) for item in value
            ]
        rendered = str(value)
        scrubbed = _redact_queries_in_text(rendered)
        return scrubbed if scrubbed != rendered else value


class AuditLogger:
    def __init__(
        self,
        path: Path | None = None,
        max_bytes: int = 0,
        backup_count: int = 0,
    ) -> None:
        if max_bytes < 0 or backup_count < 0:
            raise ValueError("audit rotation values must not be negative")
        if bool(max_bytes) != bool(backup_count):
            raise ValueError(
                "max_bytes and backup_count must either both be zero or both be positive"
            )
        if max_bytes and path is None:
            raise ValueError("audit rotation requires a file path")
        self.path = path
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._write_failed = False
        self._last_error: str | None = None
        self._probe_in_progress = False

    @property
    def healthy(self) -> bool:
        with self._state_lock:
            return not self._write_failed

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    @staticmethod
    def _line(action: str, outcome: str, fields: dict[str, Any]) -> str:
        # Applied centrally: every record passes through here, so a new call
        # site cannot forget it.
        scrubbed = _bounded_fields(fields, [_MAX_RECORD_VALUES])
        record = {
            **scrubbed,
            # Callers must never be able to replace the event identity or
            # timestamp through an overlapping metadata field.
            "ts": datetime.now(UTC).isoformat(),
            "action": _bound(action),
            "outcome": _bound(outcome),
        }
        return _cap_record(record)

    def _rotate(self, incoming_bytes: int) -> None:
        if not self.path or not self.max_bytes or not self.backup_count:
            return
        try:
            current_bytes = self.path.stat().st_size
        except FileNotFoundError:
            return
        if current_bytes == 0 or current_bytes + incoming_bytes <= self.max_bytes:
            return

        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))

    def _probe_file(self) -> None:
        """Test allocation and truncation without leaving an audit record."""
        assert self.path is not None
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_RDWR,
            0o600,
        )
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            original_size = os.fstat(descriptor).st_size
            # Opening an existing file succeeds on a full filesystem. A real
            # byte plus fsync catches ENOSPC and delayed storage failures; a
            # crash before truncation leaves only valid JSON whitespace.
            os.write(descriptor, b" ")
            os.fsync(descriptor)
            os.ftruncate(descriptor, original_size)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _append(self, payload: bytes) -> None:
        assert self.path is not None
        self._rotate(len(payload))
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written == 0:
                    raise OSError("audit file write made no progress")
                view = view[written:]
        finally:
            os.close(descriptor)

    def _write_file(self, line: str | None) -> None:
        if not self.path:
            return
        with self._io_lock:
            try:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if self.max_bytes:
                    # Rotation renames several files and replicas may share a
                    # PVC, so use a stable inter-process lock while rotating.
                    lock_path = self.path.with_name(f"{self.path.name}.lock")
                    lock_descriptor = os.open(
                        lock_path,
                        os.O_CREAT | os.O_RDWR,
                        0o600,
                    )
                    try:
                        if os.name == "posix":
                            os.fchmod(lock_descriptor, 0o600)
                        if fcntl is not None:
                            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
                        if line is None:
                            # A full but rotation-eligible active file can
                            # recover without waiting for another audit event.
                            self._rotate(1)
                            self._probe_file()
                        else:
                            self._append((line + "\n").encode("utf-8"))
                    finally:
                        os.close(lock_descriptor)
                else:
                    if line is None:
                        self._probe_file()
                    else:
                        self._append((line + "\n").encode("utf-8"))
            except OSError as exc:
                with self._state_lock:
                    first_failure = not self._write_failed
                    self._write_failed = True
                    # Keep filesystem paths and controller-specific details in
                    # logs; readiness exposes only the stable error class.
                    self._last_error = type(exc).__name__
                if first_failure:
                    log.error(
                        "Audit file %s is not writable (%s). Records continue in "
                        "the process logs; audit file health is degraded.",
                        self.path,
                        exc,
                    )
            else:
                with self._state_lock:
                    recovered = self._write_failed
                    self._write_failed = False
                    self._last_error = None
                if recovered:
                    log.warning("Audit file %s is writable again", self.path)

    def probe(self) -> None:
        """Validate configured file output without writing a fake record."""
        self._write_file(None)

    def reprobe_in_background(self) -> None:
        """Re-test a failed path without delaying a readiness response."""
        if not self.path:
            return
        with self._state_lock:
            if not self._write_failed or self._probe_in_progress:
                return
            self._probe_in_progress = True

        def run() -> None:
            try:
                self.probe()
            finally:
                with self._state_lock:
                    self._probe_in_progress = False

        threading.Thread(
            target=run,
            name="audit-file-reprobe",
            daemon=True,
        ).start()

    def emit(self, action: str, outcome: str, **fields: Any) -> None:
        line = self._line(action, outcome, fields)
        log.info("AUDIT %s", line)
        self._write_file(line)

    async def emit_async(self, action: str, outcome: str, **fields: Any) -> None:
        """Write optional file output without blocking the MCP event loop."""
        line = self._line(action, outcome, fields)
        log.info("AUDIT %s", line)
        if self.path:
            await asyncio.to_thread(self._write_file, line)
