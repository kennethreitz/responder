"""Structured logging with per-request context.

Provides a logging setup that automatically includes request metadata
(request ID, method, path, client IP) in every log message emitted
during request handling.

Usage::

    api = responder.API(enable_logging=True)

    # In any route or middleware:
    from responder.ext.logging import get_logger
    logger = get_logger(__name__)

    @api.route("/")
    def index(req, resp):
        logger.info("handling request")
        # => 2026-03-24 12:00:00 [INFO] app — handling request [GET /] [req:abc123] [client:127.0.0.1]
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

__all__ = ["get_logger", "RequestContext", "RequestContextFilter", "LoggingMiddleware"]

# Context variables for per-request metadata.
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_request_method: ContextVar[str] = ContextVar("request_method", default="-")
_request_path: ContextVar[str] = ContextVar("request_path", default="-")
_client_ip: ContextVar[str] = ContextVar("client_ip", default="-")


class RequestContext:
    """Read-only access to the current request's logging context."""

    @staticmethod
    def get_request_id() -> str:
        return _request_id.get()

    @staticmethod
    def get_method() -> str:
        return _request_method.get()

    @staticmethod
    def get_path() -> str:
        return _request_path.get()

    @staticmethod
    def get_client_ip() -> str:
        return _client_ip.get()


class RequestContextFilter(logging.Filter):
    """A logging filter that injects request context into log records.

    Adds ``request_id``, ``request_method``, ``request_path``, and
    ``client_ip`` attributes to every log record, so they can be used
    in format strings.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()  # type: ignore[attr-defined]
        record.request_method = _request_method.get()  # type: ignore[attr-defined]
        record.request_path = _request_path.get()  # type: ignore[attr-defined]
        record.client_ip = _client_ip.get()  # type: ignore[attr-defined]
        return True


# Default format that includes request context.
DEFAULT_LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s "
    "[%(request_method)s %(request_path)s] "
    "[req:%(request_id)s] [client:%(client_ip)s]"
)


def get_logger(name: str | None = None) -> logging.Logger:
    """Get a logger with the request context filter attached.

    :param name: Logger name (typically ``__name__``).
    :returns: A :class:`logging.Logger` with request context available.
    """
    logger = logging.getLogger(name)
    # Avoid adding duplicate filters.
    if not any(isinstance(f, RequestContextFilter) for f in logger.filters):
        logger.addFilter(RequestContextFilter())
    return logger


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with request-context-aware formatting.

    :param level: The logging level (default ``INFO``).
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Only add our handler if the root logger has no handlers yet,
    # or if none of them use our filter.
    has_context_handler = any(
        any(isinstance(f, RequestContextFilter) for f in h.filters)
        for h in root.handlers
    )
    if not has_context_handler:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        handler.addFilter(RequestContextFilter())
        handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
        root.addHandler(handler)


class LoggingMiddleware:
    """ASGI middleware that sets per-request context variables.

    For each HTTP request, this middleware:

    1. Extracts or generates a request ID
    2. Sets context variables for method, path, and client IP
    3. Logs the request and response with timing information
    """

    def __init__(self, app, logger_name: str = "responder.access"):
        self.app = app
        self.logger = get_logger(logger_name)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract request metadata.
        headers = dict(scope.get("headers", []))
        request_id = (
            headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())[:8]
        )
        method = scope.get("method", "WS")
        path = scope.get("path", "/")
        client = scope.get("client")
        client_ip = client[0] if client else "-"

        # Set context variables for the duration of this request.
        tok_id = _request_id.set(request_id)
        tok_method = _request_method.set(method)
        tok_path = _request_path.set(path)
        tok_ip = _client_ip.set(client_ip)

        # Track response status.
        status_code = None

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status")
                # Inject request ID into response headers.
                headers_list = list(message.get("headers", []))
                headers_list.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers_list}
            await send(message)

        import time

        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            if scope["type"] == "http":
                self.logger.info(
                    "%s %s → %s (%.1fms)",
                    method,
                    path,
                    status_code or "?",
                    duration_ms,
                )
            # Reset context variables.
            _request_id.reset(tok_id)
            _request_method.reset(tok_method)
            _request_path.reset(tok_path)
            _client_ip.reset(tok_ip)
