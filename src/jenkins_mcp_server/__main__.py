from __future__ import annotations

import argparse
import logging

from .config import get_settings
from .health import start_health_server
from .server import get_audit_logger, get_jenkins_contact, mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "streamable-http"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    transport = args.transport or settings.transport
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        start_health_server(settings, get_audit_logger(), get_jenkins_contact())
        # In mcp 2.x the listener and transport options are arguments to the
        # transport call rather than mutable attributes on mcp.settings.
        mcp.run(
            transport="streamable-http",
            host=settings.host,
            port=settings.port,
            streamable_http_path=settings.mount_path,
            stateless_http=True,
        )


if __name__ == "__main__":
    main()
