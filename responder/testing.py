"""Small testing helpers for Responder applications."""

from __future__ import annotations

from typing import Any


def assert_problem(response, status: int | None = None, **expected: Any) -> dict:
    """Assert that ``response`` is an ``application/problem+json`` response.

    Returns the decoded payload so tests can make additional assertions.
    """
    if status is not None:
        assert response.status_code == status
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json")
    payload = response.json()
    assert payload["type"]
    assert isinstance(payload["title"], str)
    assert payload["status"] == response.status_code
    for key, value in expected.items():
        assert payload[key] == value
    return payload
