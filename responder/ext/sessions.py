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
import logging
import os
import secrets
import time
from http.cookies import SimpleCookie
from typing import Protocol, runtime_checkable

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders

from ..statics import DEFAULT_SECRET_KEY


@runtime_checkable
class SessionBackend(Protocol):
    """A synchronous server-side session store.

    Optionally implement ``touch(session_id, max_age)`` to slide the TTL of an
    unchanged session without re-serializing it.
    """

    def get(self, session_id: str) -> dict | None: ...
    def set(self, session_id: str, data: dict, max_age: int) -> None: ...
    def delete(self, session_id: str) -> None: ...


@runtime_checkable
class AsyncSessionBackend(Protocol):
    """An async-native server-side session store (awaited directly, no thread).

    Optionally implement ``atouch(session_id, max_age)`` for sliding TTL.
    """

    async def aget(self, session_id: str) -> dict | None: ...
    async def aset(self, session_id: str, data: dict, max_age: int) -> None: ...
    async def adelete(self, session_id: str) -> None: ...

logger = logging.getLogger("responder")

ENV_SECRET_KEY = "RESPONDER_SECRET_KEY"  # noqa: S105 - env var name, not a secret
MIN_KEY_LENGTH = 16


class SessionConfigError(ValueError):
    """Raised for an unsafe or contradictory session configuration."""


def resolve_secret_key(secret_key, *, sessions, debug):
    """Resolve the cookie-session signing key, securely by default.

    Order: explicit ``secret_key`` → ``RESPONDER_SECRET_KEY`` env → (for
    ``sessions="auto"``) a random per-process key with a loud warning. The old
    public ``"NOTASECRET"`` default is rejected outright. ``sessions=True`` with
    no key is a hard error (strict mode).
    """
    if not secret_key:
        secret_key = os.environ.get(ENV_SECRET_KEY) or None
    if secret_key is not None:
        if secret_key == DEFAULT_SECRET_KEY:
            raise SessionConfigError(
                "secret_key='NOTASECRET' is the old public default and is no "
                "longer accepted — anyone can forge sessions signed with it. "
                'Generate a real key: python -c "import secrets; '
                'print(secrets.token_urlsafe(32))" and pass API(secret_key=...) '
                "or set RESPONDER_SECRET_KEY."
            )
        if len(secret_key) < MIN_KEY_LENGTH:
            logger.warning(
                "Responder session secret_key is only %d chars; use >= %d "
                "random characters for a secure signature.",
                len(secret_key),
                MIN_KEY_LENGTH,
            )
        return secret_key
    # No key anywhere.
    if sessions is True:  # strict refuse mode
        raise SessionConfigError(
            "Cookie sessions are enabled (sessions=True) but no secret_key was "
            "set. Pass API(secret_key=...) or set RESPONDER_SECRET_KEY, or use "
            "sessions='auto' to auto-generate an ephemeral per-process key."
        )
    key = secrets.token_urlsafe(32)  # sessions == "auto"
    if debug:
        logger.warning(
            "Responder generated an ephemeral session key (debug); sessions "
            "reset on reload. Set secret_key for stable sessions."
        )
    else:
        logger.warning(
            "Responder generated a RANDOM per-process session key because no "
            "secret_key was set. Sessions are securely signed but do NOT survive "
            "a restart and are NOT shared across workers (load-balanced users get "
            "logged out). Set API(secret_key=...) / RESPONDER_SECRET_KEY in "
            "production, or sessions=False."
        )
    return key


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

    def touch(self, session_id, max_age):
        record = self._store.get(session_id)
        if record is not None:
            data, _ = record
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

    def touch(self, session_id, max_age):
        self.client.expire(self.prefix + session_id, max_age)

    def delete(self, session_id):
        self.client.delete(self.prefix + session_id)


class AsyncRedisSessionBackend:
    """Async-native Redis session store (uses ``redis.asyncio``).

    Pass an existing ``redis.asyncio`` client, or a ``url`` to create one.
    Awaited directly by the middleware — no thread-pool hop.
    """

    def __init__(self, client=None, *, url=None, prefix="responder:session:"):
        if client is None:
            try:
                from redis import asyncio as aioredis
            except ImportError as exc:
                raise ImportError(
                    "redis is required for AsyncRedisSessionBackend: pip install redis"
                ) from exc
            client = aioredis.Redis.from_url(url or "redis://localhost:6379/0")
        self.client = client
        self.prefix = prefix

    async def aget(self, session_id):
        raw = await self.client.get(self.prefix + session_id)
        return None if raw is None else json.loads(raw)

    async def aset(self, session_id, data, max_age):
        await self.client.setex(self.prefix + session_id, max_age, json.dumps(data))

    async def atouch(self, session_id, max_age):
        await self.client.expire(self.prefix + session_id, max_age)

    async def adelete(self, session_id):
        await self.client.delete(self.prefix + session_id)


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

    async def _touch(self, session_id, data, max_age):
        """Slide the TTL of an unchanged session without re-serializing it."""
        if hasattr(self.backend, "atouch"):
            await self.backend.atouch(session_id, max_age)
        elif hasattr(self.backend, "touch"):
            await run_in_threadpool(self.backend.touch, session_id, max_age)
        else:
            await self._set(session_id, data, max_age)  # no touch -> full re-write

    @staticmethod
    def _changed(session, initial):
        try:
            return session != initial  # dict.__eq__ is deep + order-independent
        except Exception:
            return True  # uncomparable -> assume dirty, write

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        session_id = self._session_id_from(scope)
        initial = await self._get(session_id) if session_id else None
        had_session = initial is not None
        # Independent deep copy: the live session must not alias the backend's
        # stored object, and `initial` stays a pristine baseline for dirty checks.
        scope["session"] = copy.deepcopy(initial) if had_session else {}
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
                    # Persist only when changed; otherwise just slide the TTL.
                    # Either way the cookie Max-Age is refreshed below, so cookie
                    # and backend expiry stay in lock-step.
                    if not had_session or self._changed(session, initial):
                        await self._set(session_id, session, self.max_age)
                    else:
                        await self._touch(session_id, session, self.max_age)
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
