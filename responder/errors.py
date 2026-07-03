from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from typing import Any

from starlette.exceptions import HTTPException

PROBLEM_JSON = "application/problem+json"
INTERNAL_SERVER_ERROR = "Internal Server Error"
logger = logging.getLogger("responder.errors")

_STATUS_TITLES = {
    # Python's stdlib phrase table varies here across supported versions.
    # RFC 9110 renamed 413 from "Request Entity Too Large" to "Content Too Large".
    413: "Content Too Large",
}


class Problem(HTTPException):
    """A raisable RFC 9457 problem-details error.

    Raise it from any view, hook, or dependency to short-circuit the request
    with a full ``application/problem+json`` response, rendered through the
    same pipeline as framework errors — content negotiation, API-level
    ``problem_handler`` enrichment, request IDs, and the app's JSON encoder::

        from responder import Problem

        @api.route("/quota")
        def quota(req, resp):
            raise Problem(
                409,
                "You have used all 100 requests for today.",
                title="Quota Exceeded",
                type="https://api.example.com/errors/quota-exceeded",
                balance=0,
            )

    Because it subclasses Starlette's ``HTTPException``, existing exception
    handlers and middleware treat it exactly like :func:`~responder.abort`.
    When ``instance`` is omitted, the rendered payload defaults it to the
    request path per the RFC 9457 recommendation. With
    ``API(problem_details=False)`` the legacy content-negotiated error format
    is used instead and only ``status_code``/``detail`` apply.

    :param status_code: The HTTP status code (e.g. ``409``).
    :param detail: Human-readable explanation of this occurrence; defaults to
        the status phrase.
    :param title: Short summary of the problem type; defaults to the status
        phrase.
    :param type: URI identifying the problem type (default ``about:blank``).
    :param instance: URI for this specific occurrence; defaults to the
        request path when rendered.
    :param errors: Optional list of structured error dicts (the same shape
        validation failures use).
    :param headers: Optional dict of headers to attach to the error response.
    :param extensions: Any extra keyword arguments become top-level extension
        members of the problem payload.
    """

    def __init__(
        self,
        status_code: int,
        detail: str | None = None,
        *,
        title: str | None = None,
        type: str | None = None,  # noqa: A002
        instance: str | None = None,
        errors: list[dict] | None = None,
        headers: dict[str, str] | None = None,
        **extensions: Any,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.title = title
        self.type = type
        self.instance = instance
        self.errors = errors
        self.extensions = extensions


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


def _problem_handler_args(handler, payload, request, exc):
    """Shape the positional arguments for a user problem-details hook."""
    try:
        params = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        return (payload, request, exc)
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
        return (payload, request, exc)
    return (payload, request, exc)[: len(positional)]


def _call_problem_handler(handler, payload, request, exc):
    """Call a user problem-details hook with a forgiving positional shape."""
    result = handler(*_problem_handler_args(handler, payload, request, exc))
    return payload if result is None else result


async def _call_problem_handler_async(handler, payload, request, exc):
    """Like ``_call_problem_handler``, but awaits an ``async def`` hook."""
    result = handler(*_problem_handler_args(handler, payload, request, exc))
    if inspect.isawaitable(result):
        result = await result
    return payload if result is None else result


def _run_coroutine_blocking(coro):
    """Run ``coro`` to completion from synchronous code.

    When no event loop is running in this thread (e.g. a sync view executing
    in the threadpool), ``asyncio.run`` suffices. When called from a
    synchronous frame on the event-loop thread (route-level error paths,
    ``resp.problem()`` in an async view), the coroutine runs on a private
    loop in a short-lived worker thread and we block for the result — the
    same cost as running a synchronous handler inline, and error paths are
    rare by construction.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _base_problem_payload(
    scope: Mapping[str, Any] | None,
    status_code: int,
    detail: str | None,
    title: str | None,
    errors: list[dict] | None,
) -> tuple[dict[str, Any], Any]:
    """The unenriched payload for ``scope``, plus the API's problem handler."""
    payload = problem_payload(status_code, detail, title=title, errors=errors)
    request_id = _request_id_from_scope(scope)
    if request_id:
        payload["request_id"] = request_id
    api = scope.get("api") if scope else None
    return payload, getattr(api, "problem_handler", None)


def _validated_enrichment(payload: dict[str, Any], enriched: Any) -> dict[str, Any]:
    if not isinstance(enriched, dict):
        logger.warning("problem_handler returned %r; using original payload", enriched)
        return payload
    return enriched


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
    payload, handler = _base_problem_payload(scope, status_code, detail, title, errors)
    if handler is None:
        return payload
    try:
        if inspect.iscoroutinefunction(handler):
            # Synchronous call site (route-level error paths, ``resp.problem()``)
            # with an ``async def`` hook: run it to completion anyway. Truly
            # async call sites await it via ``problem_payload_for_async``.
            enriched = _run_coroutine_blocking(
                _call_problem_handler_async(handler, dict(payload), request, exc)
            )
        else:
            enriched = _call_problem_handler(handler, dict(payload), request, exc)
    except Exception:
        logger.exception("problem_handler failed; using original payload")
        return payload
    return _validated_enrichment(payload, enriched)


async def problem_payload_for_async(
    scope: Mapping[str, Any] | None,
    status_code: int,
    detail: str | None = None,
    *,
    title: str | None = None,
    errors: list[dict] | None = None,
    request: Any = None,
    exc: Any = None,
) -> dict[str, Any]:
    """Async variant of :func:`problem_payload_for` that awaits an ``async
    def`` ``problem_handler`` (synchronous handlers keep working)."""
    payload, handler = _base_problem_payload(scope, status_code, detail, title, errors)
    if handler is None:
        return payload
    try:
        enriched = await _call_problem_handler_async(handler, dict(payload), request, exc)
    except Exception:
        logger.exception("problem_handler failed; using original payload")
        return payload
    return _validated_enrichment(payload, enriched)


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
