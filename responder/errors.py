from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

PROBLEM_JSON = "application/problem+json"
INTERNAL_SERVER_ERROR = "Internal Server Error"

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
