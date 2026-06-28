"""Public type aliases for Responder handlers, hooks, and dependencies.

These are conveniences for annotating your own code::

    from responder.types import Handler, Hook

    def auth_check(req, resp) -> None: ...

They intentionally use ``...`` parameter lists, because handlers receive a
variable set of keyword arguments (path parameters, injected dependencies, and
type-hinted body models) beyond ``req`` and ``resp``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import Request, Response

__all__ = ["Handler", "Hook", "Dependency", "Request", "Response"]

#: A view handler. Receives ``(req, resp, **extras)`` and may return ``None`` or
#: a body value (``dict``/``list``/``str``/``bytes``/model, or a Flask-style
#: ``(body, status[, headers])`` tuple). Sync or async.
Handler = Callable[..., Any]

#: A before/after-request hook, called ``(req, resp)``. Sync or async.
Hook = Callable[[Request, Response], Any]

#: A dependency provider. Called with no arguments or the current ``Request``;
#: may be a function, coroutine, or (async) generator with teardown.
Dependency = Callable[..., Any]
