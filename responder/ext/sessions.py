"""Server-side session storage for Responder.

The default cookie sessions store (signed) data in the cookie itself —
fine for small payloads, but capped at ~4KB and impossible to revoke
server-side. These backends keep only an opaque session ID in the cookie
and the data on the server::

    from responder.ext.sessions import MemorySessionBackend

    api = responder.API(session_backend=MemorySessionBackend())

    @api.route("/login", methods=["POST"])
    async def login(req, resp):
        req.session["user"] = "kenneth"

For multi-process deployments, use :class:`RedisSessionBackend`.
"""

from __future__ import annotations

import copy
import json
import secrets
import time
from http.cookies import SimpleCookie

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders


class MemorySessionBackend:
    """In-process session store. Sessions vanish on restart."""

    def __init__(self):
        self._store: dict[str, tuple[dict, float]] = {}

    def get(self, session_id):
        record = self._store.get(session_id)
        if record is None:
            return None
        data, expires = record
        if time.time() > expires:
            del self._store[session_id]
            return None
        return data

    def set(self, session_id, data, max_age):
        self._store[session_id] = (data, time.time() + max_age)

    def delete(self, session_id):
        self._store.pop(session_id, None)


class RedisSessionBackend:
    """Redis-backed session store, shared across processes.

    Pass an existing client, or a ``url`` to create one (requires the
    ``redis`` package).
    """

    def __init__(self, client=None, *, url=None, prefix="responder:session:"):
        if client is None:
            try:
                import redis
            except ImportError as exc:
                raise ImportError(
                    "redis is required for RedisSessionBackend: pip install redis"
                ) from exc
            client = redis.Redis.from_url(url or "redis://localhost:6379/0")
        self.client = client
        self.prefix = prefix

    def get(self, session_id):
        raw = self.client.get(self.prefix + session_id)
        if raw is None:
            return None
        return json.loads(raw)

    def set(self, session_id, data, max_age):
        self.client.setex(self.prefix + session_id, max_age, json.dumps(data))

    def delete(self, session_id):
        self.client.delete(self.prefix + session_id)


class ServerSessionMiddleware:
    """ASGI middleware storing session data in a backend, keyed by an
    opaque cookie. A drop-in alternative to cookie-payload sessions."""

    def __init__(
        self,
        app,
        backend,
        cookie_name="responder_session",
        max_age=14 * 24 * 3600,
        path="/",
        same_site="lax",
        https_only=False,
    ):
        self.app = app
        self.backend = backend
        self.cookie_name = cookie_name
        self.max_age = max_age
        self.path = path
        self.same_site = same_site
        self.https_only = https_only

    def _session_id_from(self, scope):
        for key, value in scope.get("headers", []):
            if key == b"cookie":
                cookie: SimpleCookie = SimpleCookie(value.decode("latin-1"))
                morsel = cookie.get(self.cookie_name)
                if morsel is not None:
                    return morsel.value
        return None

    def _cookie_header(self, value, max_age):
        parts = [
            f"{self.cookie_name}={value}",
            f"Path={self.path}",
            f"Max-Age={max_age}",
            "HttpOnly",
            f"SameSite={self.same_site}",
        ]
        if self.https_only:
            parts.append("Secure")
        return "; ".join(parts)

    async def _get(self, session_id):
        if hasattr(self.backend, "aget"):
            return await self.backend.aget(session_id)
        return await run_in_threadpool(self.backend.get, session_id)

    async def _set(self, session_id, data, max_age):
        if hasattr(self.backend, "aset"):
            return await self.backend.aset(session_id, data, max_age)
        return await run_in_threadpool(self.backend.set, session_id, data, max_age)

    async def _delete(self, session_id):
        if hasattr(self.backend, "adelete"):
            return await self.backend.adelete(session_id)
        return await run_in_threadpool(self.backend.delete, session_id)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        session_id = self._session_id_from(scope)
        initial = await self._get(session_id) if session_id else None
        scope["session"] = dict(initial) if initial else {}
        # Independent deep snapshot so an unchanged session skips the
        # write-back, while nested mutations are still detected.
        initial_data = copy.deepcopy(initial) if initial else {}
        had_session = initial is not None
        # A presented-but-unresolved cookie must not be reused as the stored
        # ID — mint a fresh one to defeat session fixation.
        if session_id is not None and not had_session:
            session_id = None

        if scope["type"] == "websocket":
            # Sessions are read-only over WebSockets (no response to attach
            # a cookie to).
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            nonlocal session_id, had_session
            if message["type"] == "http.response.start":
                session = scope["session"]
                headers = MutableHeaders(scope=message)
                # Explicit rotation (regenerate_session) drops the old id even
                # if it was valid — defeats a planted-but-valid session id.
                regenerate = scope.get("_session_regenerate", False)
                if regenerate and session_id is not None:
                    await self._delete(session_id)
                    session_id = None
                    had_session = False
                if session:
                    if session_id is None:
                        session_id = secrets.token_urlsafe(32)
                    if session != initial_data or not had_session or regenerate:
                        await self._set(session_id, session, self.max_age)
                    headers.append(
                        "Set-Cookie", self._cookie_header(session_id, self.max_age)
                    )
                elif had_session:
                    await self._delete(session_id)
                    headers.append("Set-Cookie", self._cookie_header("null", 0))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def regenerate_session(req):
    """Rotate the server-side session ID, keeping the current session data.

    Call this right after a privilege change (e.g. login) to defeat session
    fixation: the old ID is discarded and a fresh one is issued. Only affects
    apps using a server-side ``session_backend``.

    Usage::

        from responder.ext.sessions import regenerate_session

        @api.route("/login", methods=["POST"])
        async def login(req, resp):
            req.session["user"] = "kenneth"
            regenerate_session(req)
    """
    req._starlette.scope["_session_regenerate"] = True
