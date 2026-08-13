from __future__ import annotations

import json
import logging
import threading
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
    """Health server that cannot be held open indefinitely.

    ThreadingHTTPServer spawns a thread per connection with no limit and no
    socket timeout, so a client that opens connections and never completes a
    request holds a thread each. The health port is reachable from anywhere the
    NetworkPolicy admits, and a probe endpoint is the one thing guaranteed to be
    listening, so this must not be the cheapest way to exhaust the process.
    """

    daemon_threads = True
    # Refuse rather than queue without bound when the backlog is full.
    request_queue_size = 32

    def __init__(self, *args: Any, max_connections: int = 64, **kwargs: Any) -> None:
        self._slots = threading.Semaphore(max_connections)
        super().__init__(*args, **kwargs)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        # Non-blocking: over the cap, close immediately instead of parking a
        # thread. A monitoring probe retries; a client holding connections open
        # gets nothing to hold.
        if not self._slots.acquire(blocking=False):
            log.warning("health: connection limit reached, refusing %s", client_address)
            self.shutdown_request(request)
            return
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A client that disconnects mid-response is routine for a probe, and
        # the default handler prints a full traceback per occurrence.
        log.debug("health: client %s disconnected", client_address)


class HealthHandler(BaseHTTPRequestHandler):
    # Without this a half-sent request line holds its thread forever.
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
) -> ThreadingHTTPServer:
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
