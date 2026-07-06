"""Session-bound CSRF protection (synchronizer-token pattern).

Enabled per application with ``API(csrf=True)`` — off by default. Unsafe
requests (``POST``/``PUT``/``PATCH``/``DELETE``) must then present the
session's token, either in an ``X-CSRF-Token`` header or a ``csrf_token``
form field; anything else gets a ``403``. Tokens come from
``req.csrf_token`` (or ``req.csrf_input`` for templates) and live in the
signed session, so they survive exactly as long as the session does.
"""

from __future__ import annotations

import hmac
import secrets
from typing import TYPE_CHECKING, Any, MutableMapping

from starlette.exceptions import HTTPException

if TYPE_CHECKING:
    from .models import Request

__all__ = ["CSRF_FIELD_NAME", "CSRF_HEADER_NAME", "enforce_csrf", "get_csrf_token"]

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FIELD_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

#: Methods that must be side-effect free (RFC 7231 §4.2.1) and therefore
#: need no CSRF token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: Content types whose body is a browser-submittable form — the only bodies
#: worth searching for a token field. Anything else (JSON, msgpack, …) can't
#: be sent cross-site by a plain HTML form, and callers sophisticated enough
#: to send it can set the header.
_FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


def get_csrf_token(session: MutableMapping[str, Any]) -> str:
    """Return the session's CSRF token, minting one on first use.

    Storing the token mutates the session, so the session middleware
    persists it to the (signed) cookie on the way out.
    """
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


async def _submitted_token(request: Request) -> str | None:
    header = request.headers.get(CSRF_HEADER_NAME)
    if header:
        return header
    content_type = request.headers.get("Content-Type", "").lower()
    if any(ct in content_type for ct in _FORM_CONTENT_TYPES):
        # Shares the request's single (streaming) form parse: the handler's
        # own media("form")/media("files")/marker reads hit the cache.
        form = await request._parsed_form()
        value = form.get(CSRF_FIELD_NAME)
        if isinstance(value, str):
            return value
    return None


async def enforce_csrf(request: Request) -> None:
    """Reject an unsafe request that lacks the session's CSRF token.

    Raises ``HTTPException(403)`` when the token is absent or wrong; safe
    methods pass through untouched.
    """
    if request.method in SAFE_METHODS:
        return
    expected = request.session.get(CSRF_SESSION_KEY)
    submitted = await _submitted_token(request)
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        raise HTTPException(
            status_code=403,
            detail=(
                "CSRF token missing or invalid. Send the value of "
                "req.csrf_token in an X-CSRF-Token header or a csrf_token "
                "form field."
            ),
        )
