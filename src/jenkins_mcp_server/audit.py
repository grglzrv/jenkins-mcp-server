from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("jenkins_mcp.audit")


class AuditLogger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        # One log line per process, not one per action, if the path is broken.
        self._write_failed = False

    def emit(self, action: str, outcome: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "action": action,
            "outcome": outcome,
            **fields,
        }
        line = json.dumps(record, sort_keys=True, default=str)
        log.info("AUDIT %s", line)
        if not self.path:
            return
        # emit() runs after the Jenkins call has already happened, so raising
        # here would report a failure for an action that succeeded, and the
        # caller would reasonably retry it. The log line above is still on
        # stdout, which is where a cluster collects it, so the record is not
        # lost; the file is the redundant copy.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            if not self._write_failed:
                self._write_failed = True
                log.error(
                    "Audit file %s is not writable (%s). Records continue on "
                    "stdout; this is reported once.",
                    self.path,
                    exc,
                )
