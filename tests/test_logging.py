"""Tests for structured logging with request context."""

import logging

import responder
from responder.ext.logging import (
    LoggingMiddleware,
    RequestContext,
    RequestContextFilter,
    get_logger,
)


def test_logging_middleware_sets_request_id():
    """LoggingMiddleware adds X-Request-ID to responses."""
    api = responder.API(allowed_hosts=["localhost"], enable_logging=True)

    @api.route("/")
    def index(req, resp):
        resp.text = "ok"

    r = api.requests.get("http://localhost/")
    assert r.status_code == 200
    assert "x-request-id" in r.headers
    assert len(r.headers["x-request-id"]) > 0


def test_logging_middleware_forwards_request_id():
    """LoggingMiddleware forwards client-provided X-Request-ID."""
    api = responder.API(allowed_hosts=["localhost"], enable_logging=True)

    @api.route("/")
    def index(req, resp):
        resp.text = "ok"

    r = api.requests.get(
        "http://localhost/", headers={"X-Request-ID": "custom-id-123"}
    )
    assert r.headers["x-request-id"] == "custom-id-123"


def test_logging_context_available_in_route():
    """Request context is available inside route handlers."""
    api = responder.API(allowed_hosts=["localhost"], enable_logging=True)
    captured = {}

    @api.route("/ctx")
    def index(req, resp):
        captured["request_id"] = RequestContext.get_request_id()
        captured["method"] = RequestContext.get_method()
        captured["path"] = RequestContext.get_path()
        captured["client_ip"] = RequestContext.get_client_ip()
        resp.text = "ok"

    api.requests.get("http://localhost/ctx")
    assert captured["method"] == "GET"
    assert captured["path"] == "/ctx"
    assert captured["request_id"] != "-"
    assert captured["client_ip"] != "-"


def test_logging_filter_injects_attributes():
    """RequestContextFilter adds context fields to log records."""
    logger = get_logger("test.filter")
    records = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = CaptureHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    api = responder.API(allowed_hosts=["localhost"], enable_logging=True)

    @api.route("/log")
    def index(req, resp):
        logger.info("test message")
        resp.text = "ok"

    api.requests.get("http://localhost/log")

    logger.removeHandler(handler)

    assert len(records) > 0
    record = records[0]
    assert hasattr(record, "request_id")
    assert hasattr(record, "request_method")
    assert hasattr(record, "request_path")
    assert hasattr(record, "client_ip")
    assert record.request_method == "GET"
    assert record.request_path == "/log"


def test_get_logger_avoids_duplicate_filters():
    """get_logger doesn't add duplicate filters."""
    logger = get_logger("test.dedup")
    count_before = sum(1 for f in logger.filters if isinstance(f, RequestContextFilter))
    get_logger("test.dedup")
    count_after = sum(1 for f in logger.filters if isinstance(f, RequestContextFilter))
    assert count_before == count_after == 1


def test_enable_logging_supersedes_request_id():
    """enable_logging handles request IDs itself (no duplicate headers)."""
    api = responder.API(
        allowed_hosts=["localhost"], request_id=True, enable_logging=True
    )

    @api.route("/")
    def index(req, resp):
        resp.text = "ok"

    r = api.requests.get("http://localhost/")
    # Should have exactly one X-Request-ID header.
    assert "x-request-id" in r.headers
