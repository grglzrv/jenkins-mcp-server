from __future__ import annotations

import argparse
import logging

from .config import get_settings
from .health import start_health_server
from .server import mcp


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
        start_health_server(settings)
        # FastMCP serves Streamable HTTP at the configured path.
        mcp.settings.host = settings.host
        mcp.settings.port = settings.port
        mcp.settings.streamable_http_path = settings.mount_path
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
