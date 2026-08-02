from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)


def readiness(settings: Settings) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {
        "jenkins_url_configured": bool(settings.jenkins_url),
        "jenkins_username_configured": bool(settings.jenkins_username),
        "jenkins_token_configured": bool(settings.jenkins_token),
    }
    if settings.jenkins_ca_bundle:
        checks["jenkins_ca_bundle_exists"] = Path(settings.jenkins_ca_bundle).is_file()
    ready = all(bool(value) for value in checks.values())
    return ready, {"status": "ready" if ready else "not_ready", "checks": checks}


class HealthHandler(BaseHTTPRequestHandler):
    settings: Settings

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"status": "ok"})
            return
        if self.path == "/readyz":
            ready, payload = readiness(self.settings)
            self._json(200 if ready else 503, payload)
            return
        self._json(404, {"error": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("health: " + format, *args)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_health_server(settings: Settings) -> ThreadingHTTPServer:
    handler = type("ConfiguredHealthHandler", (HealthHandler,), {"settings": settings})
    server = ThreadingHTTPServer((settings.health_host, settings.health_port), handler)
    thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    log.info(
        "Health endpoints listening on http://%s:%s",
        settings.health_host,
        settings.health_port,
    )
    return server
