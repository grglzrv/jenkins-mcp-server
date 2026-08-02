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

    def emit(self, action: str, outcome: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "action": action,
            "outcome": outcome,
            **fields,
        }
        log.info("AUDIT %s", json.dumps(record, sort_keys=True, default=str))
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
