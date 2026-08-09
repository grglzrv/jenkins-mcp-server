from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("jenkins_mcp.audit")


class AuditLogger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._write_failed = False
        self._last_error: str | None = None

    @property
    def healthy(self) -> bool:
        with self._lock:
            return not self._write_failed

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    @staticmethod
    def _line(action: str, outcome: str, fields: dict[str, Any]) -> str:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "action": action,
            "outcome": outcome,
            **fields,
        }
        return json.dumps(record, sort_keys=True, default=str)

    def _write_file(self, line: str | None) -> None:
        if not self.path:
            return
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    if line is not None:
                        fh.write(line + "\n")
            except OSError as exc:
                self._last_error = str(exc)
                if not self._write_failed:
                    self._write_failed = True
                    log.error(
                        "Audit file %s is not writable (%s). Records continue in "
                        "the process logs; readiness is degraded.",
                        self.path,
                        exc,
                    )
            else:
                if self._write_failed:
                    log.warning("Audit file %s is writable again", self.path)
                self._write_failed = False
                self._last_error = None

    def probe(self) -> None:
        """Validate configured file output without writing a fake record."""
        self._write_file(None)

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
