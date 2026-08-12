from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:  # POSIX containers use this to coordinate rotation across shared PVCs.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("jenkins_mcp.audit")



# Query parameters whose value is a credential rather than a locator. Jenkins
# accepts a token in a query string, and jenkins_admin_request takes a
# caller-supplied path, so a secret can arrive here as part of the URL. The
# audit stream is forwarded to a SIEM by design, which is precisely where a
# credential should not be duplicated.
_SECRET_QUERY_KEYS = frozenset(
    {
        "token",
        "api_token",
        "apitoken",
        "apikey",
        "api_key",
        "password",
        "passwd",
        "pass",
        "secret",
        "credential",
        "credentials",
        "auth",
        "authorization",
        "access_token",
        "refresh_token",
        "private_key",
        "signature",
        "sig",
        "key",
    }
)

_REDACTED = "[redacted]"


def redact_query(value: str) -> str:
    """Replace credential-looking query values, keeping the rest readable.

    Redacting the whole query would remove the detail that makes an audit
    record useful: which endpoint, with which selector. Only the values of
    known-sensitive keys are replaced, and the key names are kept so the record
    still shows what was sent.
    """
    head, separator, query = value.partition("?")
    if not separator or not query:
        return value
    parts = []
    for pair in query.split("&"):
        name, assign, _ = pair.partition("=")
        if assign and name.strip().lower() in _SECRET_QUERY_KEYS:
            parts.append(f"{name}={_REDACTED}")
        else:
            parts.append(pair)
    return f"{head}?{'&'.join(parts)}"


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
        scrubbed = {
            key: redact_query(value) if isinstance(value, str) else value
            for key, value in fields.items()
        }
        record = {
            **scrubbed,
            # Callers must never be able to replace the event identity or
            # timestamp through an overlapping metadata field.
            "ts": datetime.now(UTC).isoformat(),
            "action": action,
            "outcome": outcome,
        }
        return json.dumps(record, sort_keys=True, default=str)

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
