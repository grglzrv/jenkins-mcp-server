from __future__ import annotations

import argparse
import logging

from .audit import QueryRedactingLogFilter
from .config import get_settings
from .health import start_health_server
from .server import get_audit_logger, get_jenkins_contact, mcp


def configure_logging(verbose: bool) -> None:
    """Configure process logging without exposing arbitrary URL queries."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    query_filter = QueryRedactingLogFilter

    # HTTPX currently emits from the exact ``httpx`` logger. Keep a logger
    # filter for direct handlers, and filter the root handlers too: filters on
    # ``httpcore`` do not run for propagated records from child loggers such as
    # ``httpcore.connection``.
    for logger_name in ("httpx", "httpcore"):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(item, query_filter) for item in logger.filters):
            logger.addFilter(query_filter())
    for handler in logging.getLogger().handlers:
        if not any(isinstance(item, query_filter) for item in handler.filters):
            handler.addFilter(query_filter())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "streamable-http"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(args.verbose)
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
