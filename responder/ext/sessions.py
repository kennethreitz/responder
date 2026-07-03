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

**What a session may hold.** Treat session values as JSON-shaped data —
dicts, lists, strings, numbers, booleans, ``None`` — so the same code works
on every backend. :class:`MemorySessionBackend` stores Python objects as-is
(anything goes, but nothing is checked), while the Redis backends serialize:
their default :class:`JSONSessionSerializer` extends plain JSON to also
round-trip ``datetime``, ``date``, ``time``, ``Decimal``, ``UUID``, ``set``,
``frozenset``, and ``bytes`` values. Tuples become lists on the Redis path
(a JSON limitation); anything else needs a custom ``serializer=``.
"""

from __future__ import annotations

import base64
import copy
import datetime as _dt
import decimal
import json
import logging
import os
import secrets
import threading
import time
import uuid as _uuid
from collections import OrderedDict
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

    Session data should be treated as JSON-shaped (see the module docstring):
    backends are free to serialize, so code that stores arbitrary Python
    objects works only on stores that keep objects in memory.
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
    """In-process session store. Sessions vanish on restart.

    Keys are bounded: at most ``max_keys`` sessions are kept, with the
    earliest-expiring entries evicted beyond the cap. This stops an
    attacker who rotates session cookies from growing process memory
    without bound — previously an abandoned session was only deleted when
    ``get()`` was called with that exact ID, so it lived until restart.
    Expired entries are also swept opportunistically on writes.

    The ``OrderedDict`` is kept ordered by expiry: only operations that
    refresh the stored expiry (``set``/``touch``) move an entry to the
    back. A plain ``get()`` must not — otherwise a nearly-expired but
    recently-read entry would sit at the back while a live session near
    the front gets evicted under cap pressure.
    """

    def __init__(self, max_keys: int = 100_000):
        self._store: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def _evict(self, now: float) -> None:
        """Sweep expired entries from the front (earliest expiry), then
        enforce the cap.

        The caller must hold the lock.
        """
        while self._store:
            _, expires = next(iter(self._store.values()))
            if now > expires:
                self._store.popitem(last=False)
            else:
                break
        while self._max_keys is not None and len(self._store) > self._max_keys:
            self._store.popitem(last=False)

    def get(self, session_id):
        with self._lock:
            record = self._store.get(session_id)
            if record is None:
                return None
            data, expires = record
            if time.time() > expires:
                del self._store[session_id]
                return None
            # No move_to_end here: order == expiry order, and a read does
            # not extend the expiry (see class docstring).
            return data

    def set(self, session_id, data, max_age):
        now = time.time()
        with self._lock:
            self._store[session_id] = (data, now + max_age)
            self._store.move_to_end(session_id)
            self._evict(now)

    def touch(self, session_id, max_age):
        with self._lock:
            record = self._store.get(session_id)
            if record is not None:
                data, _ = record
                self._store[session_id] = (data, time.time() + max_age)
                self._store.move_to_end(session_id)

    def delete(self, session_id):
        with self._lock:
            self._store.pop(session_id, None)


# Tag key marking a value the default serializer has type-encoded. Namespaced
# to make collisions with real session data vanishingly unlikely. A user dict
# that nonetheless carries this key is transparently escaped on encode and
# restored on decode (see JSONSessionSerializer._escape), so round-trips stay
# lossless and can't be mis-decoded or poisoned.
_TYPE_TAG = "__responder.session.type__"


class JSONSessionSerializer:
    """JSON session codec that round-trips common non-JSON types.

    The default ``serializer`` for :class:`RedisSessionBackend` and
    :class:`AsyncRedisSessionBackend`. On top of the JSON types it
    round-trips ``datetime.datetime``, ``datetime.date``, ``datetime.time``,
    ``decimal.Decimal``, ``uuid.UUID``, ``set``, ``frozenset``, and
    ``bytes`` — so sessions that work with :class:`MemorySessionBackend`
    in development keep working when Redis backs them in production.

    Values of those types are stored as small tagged JSON objects and
    revived on read. Remaining caveats (inherent to JSON):

    - tuples are stored as lists;
    - dict keys must be strings;
    - other custom objects still raise ``TypeError`` — plug in your own
      codec via the backends' ``serializer=`` parameter (any object with
      ``dumps(data) -> str | bytes`` and ``loads(raw) -> dict`` works).
    """

    #: The exact types this codec revives, beyond native JSON.
    EXTRA_TYPES = (
        _dt.datetime,
        _dt.date,
        _dt.time,
        decimal.Decimal,
        _uuid.UUID,
        set,
        frozenset,
        bytes,
    )

    @staticmethod
    def _default(obj):
        # NOTE: datetime before date — datetime is a date subclass.
        if isinstance(obj, _dt.datetime):
            return {_TYPE_TAG: "datetime", "value": obj.isoformat()}
        if isinstance(obj, _dt.date):
            return {_TYPE_TAG: "date", "value": obj.isoformat()}
        if isinstance(obj, _dt.time):
            return {_TYPE_TAG: "time", "value": obj.isoformat()}
        if isinstance(obj, decimal.Decimal):
            return {_TYPE_TAG: "decimal", "value": str(obj)}
        if isinstance(obj, _uuid.UUID):
            return {_TYPE_TAG: "uuid", "value": str(obj)}
        if isinstance(obj, frozenset):
            # Members are encoded recursively by json itself.
            return {_TYPE_TAG: "frozenset", "value": list(obj)}
        if isinstance(obj, set):
            return {_TYPE_TAG: "set", "value": list(obj)}
        if isinstance(obj, bytes):
            return {
                _TYPE_TAG: "bytes",
                "value": base64.b64encode(obj).decode("ascii"),
            }
        raise TypeError(
            f"Object of type {type(obj).__name__} is not session-serializable; "
            "store JSON-shaped data, or pass a custom serializer= to the "
            "session backend."
        )

    # Escape prefix applied to any user dict key that collides with the
    # reserved tag key. ``_TYPE_TAG`` -> ``_ESC_PREFIX + _TYPE_TAG``, and any
    # existing run of the prefix in front of the tag gains one more level, so
    # the transform is reversible for arbitrarily-nested escapes.
    _ESC_PREFIX = "__esc__"

    _REVIVERS = {
        "datetime": _dt.datetime.fromisoformat,
        "date": _dt.date.fromisoformat,
        "time": _dt.time.fromisoformat,
        "decimal": decimal.Decimal,
        "uuid": _uuid.UUID,
        "set": set,
        "frozenset": frozenset,
        "bytes": lambda v: base64.b64decode(v),
    }

    @classmethod
    def _is_reserved_key(cls, key):
        """True for the reserved tag key or an already-escaped form of it."""
        if not isinstance(key, str):
            return False
        stripped = key
        while stripped.startswith(cls._ESC_PREFIX):
            stripped = stripped[len(cls._ESC_PREFIX) :]
        return stripped == _TYPE_TAG

    @classmethod
    def _escape(cls, data):
        """Recursively escape user dict keys that collide with the reserved tag.

        Called on the *plain* Python data before ``json.dumps`` so a user dict
        like ``{_TYPE_TAG: "datetime", "value": ...}`` can't be mistaken for an
        encoding this serializer produces. Our own tag dicts are created later
        by :meth:`_default` (for non-JSON types), so any reserved key present at
        this stage is user data and gets one escape-prefix level added.
        """
        if isinstance(data, dict):
            out = {}
            for k, v in data.items():
                new_key = cls._ESC_PREFIX + k if cls._is_reserved_key(k) else k
                out[new_key] = cls._escape(v)
            return out
        if isinstance(data, list):
            return [cls._escape(v) for v in data]
        return data

    @classmethod
    def _unescape_key(cls, key):
        """Strip one escape-prefix level from a reserved key (inverse of
        :meth:`_escape`)."""
        if cls._is_reserved_key(key) and key.startswith(cls._ESC_PREFIX):
            return key[len(cls._ESC_PREFIX) :]
        return key

    @classmethod
    def _object_hook(cls, obj):
        reviver = cls._REVIVERS.get(obj.get(_TYPE_TAG, ""))
        if reviver is not None and "value" in obj:
            try:
                return reviver(obj["value"])
            except (ValueError, TypeError, KeyError):
                # A malformed tagged value (e.g. a bad ISO string) must not
                # poison the session with a hard 500 on every read — fall back
                # to handing the raw dict through untouched.
                return obj
        # Reverse the encode-time escaping of user keys that collide with the
        # reserved tag. Only reserved keys are ever rewritten, so this is a
        # no-op for the overwhelmingly common case.
        if any(cls._is_reserved_key(k) for k in obj):
            return {cls._unescape_key(k): v for k, v in obj.items()}
        return obj

    def dumps(self, data: dict) -> str:
        """Serialize ``data`` to a JSON string."""
        return json.dumps(self._escape(data), default=self._default)

    def loads(self, raw: str | bytes) -> dict:
        """Deserialize ``raw`` (``str`` or ``bytes``) back to a dict."""
        return json.loads(raw, object_hook=self._object_hook)


class RedisSessionBackend:
    """Redis-backed session store, shared across processes.

    Pass an existing client, or a ``url`` to create one (requires the
    ``redis`` package).

    :param serializer: Codec used to store session dicts — any object with
        ``dumps(data) -> str | bytes`` and ``loads(raw) -> dict``. Defaults
        to :class:`JSONSessionSerializer`, which round-trips ``datetime``,
        ``date``, ``time``, ``Decimal``, ``UUID``, ``set``, ``frozenset``,
        and ``bytes`` on top of plain JSON.
    """

    def __init__(
        self, client=None, *, url=None, prefix="responder:session:", serializer=None
    ):
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
        self.serializer = JSONSessionSerializer() if serializer is None else serializer

    def get(self, session_id):
        raw = self.client.get(self.prefix + session_id)
        if raw is None:
            return None
        return self.serializer.loads(raw)

    def set(self, session_id, data, max_age):
        self.client.setex(
            self.prefix + session_id, max_age, self.serializer.dumps(data)
        )

    def touch(self, session_id, max_age):
        self.client.expire(self.prefix + session_id, max_age)

    def delete(self, session_id):
        self.client.delete(self.prefix + session_id)


class AsyncRedisSessionBackend:
    """Async-native Redis session store (uses ``redis.asyncio``).

    Pass an existing ``redis.asyncio`` client, or a ``url`` to create one.
    Awaited directly by the middleware — no thread-pool hop.

    :param serializer: Codec used to store session dicts — any object with
        ``dumps(data) -> str | bytes`` and ``loads(raw) -> dict``. Defaults
        to :class:`JSONSessionSerializer`.
    """

    def __init__(
        self, client=None, *, url=None, prefix="responder:session:", serializer=None
    ):
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
        self.serializer = JSONSessionSerializer() if serializer is None else serializer

    async def aget(self, session_id):
        raw = await self.client.get(self.prefix + session_id)
        return None if raw is None else self.serializer.loads(raw)

    async def aset(self, session_id, data, max_age):
        await self.client.setex(
            self.prefix + session_id, max_age, self.serializer.dumps(data)
        )

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
