from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)


class JenkinsContact:
    """Passive, process-local Jenkins transport diagnostics.

    The MCP event loop records requests while the health server reads from a
    separate thread. Messages intentionally contain only exception class names,
    never Jenkins URLs or credential-bearing exception text.
    """

    def __init__(self, warning_interval_seconds: float = 60.0) -> None:
        self._lock = threading.Lock()
        self._last_contact: float | None = None
        self._last_transport_error: str | None = None
        self._last_warning: float | None = None
        self._warning_interval_seconds = warning_interval_seconds

    def record_contact(self) -> None:
        """Record any HTTP response, regardless of its status code."""
        with self._lock:
            previous_error = self._last_transport_error
            self._last_contact = time.monotonic()
            self._last_transport_error = None
        if previous_error is not None:
            log.info("Jenkins contact recovered after %s", previous_error)

    def record_failure(self, exc: BaseException) -> None:
        """Record and rate-limit warnings for a transport failure."""
        now = time.monotonic()
        error = type(exc).__name__
        with self._lock:
            previous_error = self._last_transport_error
            self._last_transport_error = error
            should_warn = (
                previous_error != error
                or self._last_warning is None
                or now - self._last_warning >= self._warning_interval_seconds
            )
            if should_warn:
                self._last_warning = now
        if should_warn:
            log.warning("Jenkins transport failure: %s; readiness remains available", error)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last_contact = self._last_contact
            last_error = self._last_transport_error
        if last_contact is None:
            age: float | None = None
        else:
            age = round(max(0.0, time.monotonic() - last_contact), 1)
        return {
            "last_contact_age_seconds": age,
            "last_transport_error": last_error,
        }
