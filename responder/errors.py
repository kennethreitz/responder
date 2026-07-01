from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

PROBLEM_JSON = "application/problem+json"
INTERNAL_SERVER_ERROR = "Internal Server Error"
logger = logging.getLogger("responder.errors")

_STATUS_TITLES = {
    # Python's stdlib phrase table varies here across supported versions.
    # RFC 9110 renamed 413 from "Request Entity Too Large" to "Content Too Large".
    413: "Content Too Large",
}


def status_title(status_code: int) -> str:
    if status_code in _STATUS_TITLES:
        return _STATUS_TITLES[status_code]
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP Error"


def problem_payload(
    status_code: int,
    detail: str | None = None,
    *,
    title: str | None = None,
    errors: list[dict] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "about:blank",
        "title": title or status_title(status_code),
        "status": status_code,
    }
    if detail is not None:
        payload["detail"] = detail
    if errors is not None:
        payload["errors"] = errors
    return payload


def _request_id_from_scope(scope: Mapping[str, Any] | None) -> str | None:
    if not scope:
        return None
    request_id = scope.get("request_id")
    if isinstance(request_id, str) and request_id:
        return request_id
    for key, value in scope.get("headers", []):
        if key == b"x-request-id":
            return value.decode("latin-1")
    return None


def _call_problem_handler(handler, payload, request, exc):
    """Call a user problem-details hook with a forgiving positional shape."""
    try:
        params = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        args = (payload, request, exc)
    else:
        positional = [
            p
            for p in params
            if p.kind
            in (
                p.POSITIONAL_ONLY,
                p.POSITIONAL_OR_KEYWORD,
            )
        ]
        if any(p.kind == p.VAR_POSITIONAL for p in params):
            args = (payload, request, exc)
        else:
            args = (payload, request, exc)[: len(positional)]
    result = handler(*args)
    return payload if result is None else result


def problem_payload_for(
    scope: Mapping[str, Any] | None,
    status_code: int,
    detail: str | None = None,
    *,
    title: str | None = None,
    errors: list[dict] | None = None,
    request: Any = None,
    exc: Any = None,
) -> dict[str, Any]:
    """Build a problem-details payload with API-level enrichment applied."""
    payload = problem_payload(status_code, detail, title=title, errors=errors)
    request_id = _request_id_from_scope(scope)
    if request_id:
        payload["request_id"] = request_id
    api = scope.get("api") if scope else None
    handler = getattr(api, "problem_handler", None)
    if handler is None:
        return payload
    try:
        enriched = _call_problem_handler(handler, dict(payload), request, exc)
    except Exception:
        logger.exception("problem_handler failed; using original payload")
        return payload
    if not isinstance(enriched, dict):
        logger.warning("problem_handler returned %r; using original payload", enriched)
        return payload
    return enriched


def problem_bytes(
    status_code: int,
    detail: str | None = None,
    *,
    title: str | None = None,
    errors: list[dict] | None = None,
) -> bytes:
    return json.dumps(
        problem_payload(status_code, detail, title=title, errors=errors)
    ).encode("utf-8")


def problem_bytes_for(
    scope: Mapping[str, Any] | None,
    status_code: int,
    detail: str | None = None,
    *,
    title: str | None = None,
    errors: list[dict] | None = None,
    request: Any = None,
    exc: Any = None,
) -> bytes:
    return json.dumps(
        problem_payload_for(
            scope,
            status_code,
            detail,
            title=title,
            errors=errors,
            request=request,
            exc=exc,
        )
    ).encode("utf-8")


def legacy_error_payload(
    status_code: int,
    detail: str | None = None,
    *,
    title: str | None = None,
    errors: list[dict] | None = None,
) -> dict[str, Any]:
    if errors is not None:
        return {"errors": errors}
    return {"error": detail or title or status_title(status_code)}
