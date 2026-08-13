from __future__ import annotations

import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .config import Settings
from .diagnostics import JenkinsContact

log = logging.getLogger(__name__)


def readiness(
    settings: Settings,
    audit: AuditLogger | None = None,
    contact: JenkinsContact | None = None,
) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {
        "jenkins_url_configured": bool(settings.jenkins_url),
        "jenkins_username_configured": bool(settings.jenkins_username),
        "jenkins_token_configured": bool(settings.jenkins_token),
    }
    if settings.jenkins_ca_bundle:
        checks["jenkins_ca_bundle_exists"] = Path(settings.jenkins_ca_bundle).is_file()
    # Reported either way so the failure is visible in /readyz, but only
    # required when the operator has asked for it. Gating readiness on the file
    # turns a full audit volume into a service outage: every replica fails the
    # check at once, whether they share a PVC or fill identically sized
    # emptyDirs at the same rate, while the records themselves are still going
    # to the process logs.
    audit_ok = True
    if audit and audit.path:
        # A health check is the only activity guaranteed while a pod is idle.
        # Re-probe in a single background thread: a hung PVC must not delay the
        # readiness response when the redundant file is explicitly optional.
        if not audit.healthy:
            audit.reprobe_in_background()
        audit_ok = audit.healthy
        checks["audit_log_writable"] = audit_ok
        if not audit_ok and audit.last_error:
            checks["audit_log_error"] = audit.last_error

    required = [
        value
        for name, value in checks.items()
        if name not in {"audit_log_writable", "audit_log_error"}
    ]
    ready = all(bool(value) for value in required)
    if settings.audit_required_for_readiness:
        ready = ready and audit_ok

    # Reachability is reported, never required. Readiness controls Service
    # endpoints: taking every replica out because Jenkins is restarting turns
    # one upstream outage into two, and leaves callers with a refused
    # connection instead of an error naming the cause. The server can still
    # serve, and what it serves is more useful than nothing.
    #
    # Passive by design. last_contact_age_seconds is null on a pod that has not
    # been asked to do anything yet; that is honest rather than a probe result
    # invented to fill the field.
    contact_snapshot = (contact or JenkinsContact()).snapshot()
    payload: dict[str, Any] = {
        "status": "ready" if ready else "not_ready",
        "checks": checks,
        "jenkins": contact_snapshot,
    }
    return ready, payload


class BoundedHealthServer(ThreadingHTTPServer):
    """Bound health-handler threads and expire incomplete requests."""

    daemon_threads = True
    _warning_interval_seconds = 60.0

    def __init__(self, *args: Any, max_connections: int = 64, **kwargs: Any) -> None:
        self._slots = threading.BoundedSemaphore(max_connections)
        self._last_limit_warning: float | None = None
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        """Reserve capacity before ThreadingMixIn creates a handler thread."""
        if not self._slots.acquire(blocking=False):
            now = time.monotonic()
            if (
                self._last_limit_warning is None
                or now - self._last_limit_warning >= self._warning_interval_seconds
            ):
                self._last_limit_warning = now
                log.warning("health: connection limit reached; refusing excess traffic")
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            # Thread creation can fail after the slot is reserved.
            self._slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        error = sys.exc_info()[1]
        if isinstance(
            error,
            (
                BrokenPipeError,
                ConnectionAbortedError,
                ConnectionResetError,
                TimeoutError,
            ),
        ):
            # Slow/incomplete requests and probes disconnecting mid-response
            # are routine. Keep those bounded events out of stderr tracebacks.
            log.debug("health: client disconnected or timed out")
            return
        # Do not hide programming errors or unexpected handler failures.
        super().handle_error(request, client_address)


class HealthHandler(BaseHTTPRequestHandler):
    # StreamRequestHandler applies this to the accepted socket in setup().
    # Without it, a partial request line owns a handler slot indefinitely.
    timeout = 5
    settings: Settings
    audit: AuditLogger | None = None
    contact: JenkinsContact

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"status": "ok"})
            return
        if self.path == "/readyz":
            ready, payload = readiness(self.settings, self.audit, self.contact)
            self._json(200 if ready else 503, payload)
            return
        self._json(404, {"error": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("health: " + format, *args)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Readiness exposes configuration state and changes over the lifetime
        # of a pod. Monitoring proxies must not cache it, and browsers should
        # never MIME-sniff either health response as something executable.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def start_health_server(
    settings: Settings,
    audit: AuditLogger | None = None,
    contact: JenkinsContact | None = None,
) -> BoundedHealthServer:
    shared_contact = contact or JenkinsContact()
    handler = type(
        "ConfiguredHealthHandler",
        (HealthHandler,),
        {"settings": settings, "audit": audit, "contact": shared_contact},
    )
    server = BoundedHealthServer(
        (settings.health_host, settings.health_port),
        handler,
        max_connections=settings.health_max_connections,
    )
    thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    log.info(
        "Health endpoints listening on http://%s:%s",
        settings.health_host,
        settings.health_port,
    )
    return server
