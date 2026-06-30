"""Authentication helpers: Bearer, Basic, and API-key schemes.

Each scheme is a callable that authenticates a request and returns the principal
your ``verify`` callback produced (or raises ``401`` with the right
``WWW-Authenticate`` challenge). Use one as a dependency to inject the principal
into a handler::

    from responder.ext.auth import BearerAuth

    auth = BearerAuth(verify=lambda token: users.get(token))
    auth.register(api)                 # registers the OpenAPI security scheme
    api.add_dependency("user", auth)

    @api.get("/me", security=["bearerAuth"])
    async def me(req, resp, *, user):
        resp.media = {"user": user}

``verify`` may be sync or async; return a truthy principal on success or a falsy
value to reject. For static secrets, pass them directly and the scheme compares
in constant time — ``BearerAuth(tokens=[...])``, ``APIKeyAuth(keys=[...])``,
``BasicAuth(credentials={"alice": "s3cret"})``.
"""

from __future__ import annotations

import base64
import binascii
import inspect
from secrets import compare_digest
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

__all__ = [
    "AuthBase",
    "BearerAuth",
    "BasicAuth",
    "APIKeyAuth",
    "ScopedAuth",
    "OptionalAuth",
    "compare_digest",
]


async def _call(fn: Callable, *args: Any) -> Any:
    """Call ``fn`` (sync or async) with ``args``, awaiting as appropriate."""
    if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(
        getattr(fn, "__call__", None)  # noqa: B004 - inspecting __call__, not calling
    ):
        return await fn(*args)
    return await run_in_threadpool(fn, *args)


def _default_scopes(principal: Any) -> frozenset[str]:
    """Best-effort extraction of the scopes/roles a principal holds.

    Looks for a ``scopes`` or ``roles`` attribute (or mapping key), accepting a
    space-delimited string or any iterable of strings. A principal that is
    itself a (non-string) iterable of strings is treated as the scope set. Falls
    back to an empty set, so a principal that carries no scope information simply
    satisfies no scope requirement.
    """
    for attr in ("scopes", "roles"):
        value = getattr(principal, attr, None)
        if value is None and isinstance(principal, dict):
            value = principal.get(attr)
        if value is not None:
            if isinstance(value, str):
                return frozenset(value.split())
            return frozenset(value)
    if not isinstance(principal, str) and isinstance(
        principal, (list, tuple, set, frozenset)
    ):
        return frozenset(principal)
    return frozenset()


def _as_tuple(value) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _scope_set(value) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset(value.split())
    return frozenset(value)


def _matches_any(value: str, candidates: list[str]) -> bool:
    """Constant-time membership test (checks every candidate, no short-circuit)."""
    value_b = value.encode()
    matched = False
    for candidate in candidates:
        if compare_digest(value_b, candidate.encode()):
            matched = True
    return matched


class AuthBase:
    """Base class for authentication schemes.

    Subclasses implement ``_extract`` (pull the credential from the request),
    ``_verify`` (turn a credential into a principal), ``_challenge`` (the
    ``WWW-Authenticate`` value, or ``None``), and ``security_scheme`` (the
    OpenAPI definition).
    """

    scheme_name: str = "auth"

    def __init__(self, verify=None, *, auto_error=True, scheme_name=None):
        self.verify = verify
        self.auto_error = auto_error
        if scheme_name is not None:
            self.scheme_name = scheme_name

    async def __call__(self, req):  # usable directly as a dependency provider
        return await self.authenticate(req)

    async def authenticate(self, req):
        """Authenticate ``req``; return the principal or reject with ``401``."""
        credential = self._extract(req)
        if credential is None:
            return self._reject()
        principal = await self._verify(credential)
        if not principal:
            return self._reject()
        return principal

    def _reject(self):
        if not self.auto_error:
            return
        challenge = self._challenge()
        headers = {"WWW-Authenticate": challenge} if challenge else None
        raise HTTPException(
            status_code=401, detail="Not authenticated", headers=headers
        )

    def register(self, api):
        """Register this scheme with ``api``'s OpenAPI document (chainable)."""
        api.add_security_scheme(self.scheme_name, self.security_scheme())
        return self

    def requires(self, *scopes: str, roles=(), extractor=None) -> ScopedAuth:
        """Wrap this scheme to also require ``scopes`` on the principal.

        The returned :class:`ScopedAuth` authenticates exactly like ``self`` and
        then rejects with ``403`` unless the principal holds every named scope::

            admin = bearer.requires("admin")

            @api.get("/admin", auth=admin)
            def dashboard(req, resp, *, user): ...

        Pass ``extractor`` to override how scopes are read off the principal
        (default: a ``scopes``/``roles`` attribute or mapping key).
        """
        return ScopedAuth(self, scopes=scopes, roles=roles, extractor=extractor)

    def optional(self) -> OptionalAuth:
        """Accept credentials when present, but allow anonymous requests.

        Missing credentials inject ``None`` into ``user``/``principal``/``auth``
        route parameters. Invalid credentials still fail with ``401``.
        """
        return OptionalAuth(self)

    # --- subclass hooks -------------------------------------------------
    def _extract(self, req):
        raise NotImplementedError

    async def _verify(self, credential):
        raise NotImplementedError

    def _challenge(self):
        return None

    def security_scheme(self) -> dict:
        raise NotImplementedError


class BearerAuth(AuthBase):
    """``Authorization: Bearer <token>`` authentication."""

    scheme_name = "bearerAuth"

    def __init__(
        self,
        verify=None,
        *,
        tokens=None,
        bearer_format=None,
        realm=None,
        auto_error=True,
        scheme_name=None,
    ):
        super().__init__(verify, auto_error=auto_error, scheme_name=scheme_name)
        self.tokens = list(tokens) if tokens is not None else None
        self.bearer_format = bearer_format
        self.realm = realm
        if verify is None and self.tokens is None:
            raise ValueError("BearerAuth requires verify= or tokens=")

    def _extract(self, req):
        scheme, _, token = req.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return token.strip()

    async def _verify(self, token):
        if self.verify is not None:
            return await _call(self.verify, token)
        assert self.tokens is not None  # guaranteed by __init__
        return token if _matches_any(token, self.tokens) else None

    def _challenge(self):
        return f'Bearer realm="{self.realm}"' if self.realm else "Bearer"

    def security_scheme(self):
        scheme = {"type": "http", "scheme": "bearer"}
        if self.bearer_format:
            scheme["bearerFormat"] = self.bearer_format
        return scheme


class BasicAuth(AuthBase):
    """HTTP Basic (``Authorization: Basic <base64>``) authentication."""

    scheme_name = "basicAuth"

    def __init__(
        self,
        verify=None,
        *,
        credentials=None,
        realm="Restricted",
        auto_error=True,
        scheme_name=None,
    ):
        super().__init__(verify, auto_error=auto_error, scheme_name=scheme_name)
        self.credentials = dict(credentials) if credentials is not None else None
        self.realm = realm
        if verify is None and self.credentials is None:
            raise ValueError("BasicAuth requires verify= or credentials=")

    def _extract(self, req):
        scheme, _, encoded = req.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "basic" or not encoded.strip():
            return None
        try:
            decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
        username, sep, password = decoded.partition(":")
        if not sep:
            return None
        return (username, password)

    async def _verify(self, credential):
        username, password = credential
        if self.verify is not None:
            return await _call(self.verify, username, password)
        assert self.credentials is not None  # guaranteed by __init__
        expected = self.credentials.get(username)
        # Always run one comparison so a missing username and a wrong password
        # cost the same (reduces the username-enumeration timing signal).
        reference = expected if expected is not None else password
        ok = compare_digest(password.encode(), reference.encode())
        return username if (expected is not None and ok) else None

    def _challenge(self):
        return f'Basic realm="{self.realm}"'

    def security_scheme(self):
        return {"type": "http", "scheme": "basic"}


class APIKeyAuth(AuthBase):
    """API-key authentication from a header, query parameter, or cookie."""

    scheme_name = "apiKeyAuth"

    def __init__(
        self,
        verify=None,
        *,
        keys=None,
        name="X-API-Key",
        location="header",
        auto_error=True,
        scheme_name=None,
    ):
        super().__init__(verify, auto_error=auto_error, scheme_name=scheme_name)
        if location not in ("header", "query", "cookie"):
            raise ValueError("location must be 'header', 'query', or 'cookie'")
        self.keys = list(keys) if keys is not None else None
        self.name = name
        self.location = location
        if verify is None and self.keys is None:
            raise ValueError("APIKeyAuth requires verify= or keys=")

    def _extract(self, req):
        if self.location == "header":
            value = req.headers.get(self.name)
        elif self.location == "query":
            # Responder's Request has .params; a Starlette WebSocket (injected
            # for WS routes) only has .query_params. Pick by presence, not
            # truthiness — an empty params mapping is falsy but valid.
            params = getattr(req, "params", None)
            if params is None:
                params = req.query_params
            value = params.get(self.name)
        else:
            value = req.cookies.get(self.name)
        return value or None

    async def _verify(self, key):
        if self.verify is not None:
            return await _call(self.verify, key)
        assert self.keys is not None  # guaranteed by __init__
        return key if _matches_any(key, self.keys) else None

    def security_scheme(self):
        return {"type": "apiKey", "in": self.location, "name": self.name}


class ScopedAuth:
    """An auth scheme wrapped with a scope/role requirement.

    Created via :meth:`AuthBase.requires`. It authenticates through the wrapped
    scheme, then enforces that the resulting principal holds every required
    scope, rejecting with ``403`` otherwise. It proxies ``scheme_name``,
    ``security_scheme()``, and ``register()`` so it documents and registers the
    same OpenAPI security scheme as the scheme it wraps — the required scopes
    surface as the operation's security-requirement value.
    """

    def __init__(self, auth: AuthBase, scopes=(), *, roles=(), extractor=None):
        self._auth = auth
        self.required_scopes = tuple(
            dict.fromkeys((*_as_tuple(scopes), *_as_tuple(roles)))
        )
        self._extractor = extractor

    @property
    def scheme_name(self) -> str:
        return self._auth.scheme_name

    @property
    def auto_error(self) -> bool:
        return self._auth.auto_error

    def security_scheme(self) -> dict:
        return self._auth.security_scheme()

    def security_requirement(self) -> dict:
        return {self.scheme_name: list(self.required_scopes)}

    def register(self, api):
        self._auth.register(api)
        return self

    def requires(self, *scopes: str, roles=(), extractor=None) -> ScopedAuth:
        """Add further required scopes, returning a new wrapper (chainable)."""
        return ScopedAuth(
            self._auth,
            (*self.required_scopes, *scopes, *_as_tuple(roles)),
            extractor=extractor or self._extractor,
        )

    def optional(self) -> OptionalAuth:
        return OptionalAuth(self)

    async def __call__(self, req):
        return await self.authenticate(req)

    async def authenticate(self, req):
        principal = await self._auth.authenticate(req)
        if principal is None:  # auto_error=False on the wrapped scheme
            return None
        held = (
            self._extractor(principal)
            if self._extractor is not None
            else _default_scopes(principal)
        )
        held = _scope_set(held)
        req.state.scopes = held
        missing = [scope for scope in self.required_scopes if scope not in held]
        if missing:
            if not self.auto_error:
                return None
            challenge = getattr(self._auth, "_challenge", lambda: None)()
            headers = None
            if challenge:
                scope = " ".join(missing)
                headers = {
                    "WWW-Authenticate": (
                        f'{challenge} error="insufficient_scope", scope="{scope}"'
                    )
                }
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient scope: {' '.join(missing)}",
                headers=headers,
            )
        return principal


class OptionalAuth:
    """An auth wrapper that makes missing credentials anonymous.

    Invalid credentials still fail through the wrapped scheme. In OpenAPI, this
    documents both anonymous access and the wrapped security requirement.
    """

    optional_auth = True

    def __init__(self, auth: AuthBase | ScopedAuth):
        self._auth = auth

    @property
    def scheme_name(self) -> str:
        return self._auth.scheme_name

    def security_scheme(self) -> dict:
        return self._auth.security_scheme()

    def security_requirement(self) -> list[dict]:
        requirement = (
            self._auth.security_requirement()
            if hasattr(self._auth, "security_requirement")
            else {self.scheme_name: []}
        )
        return [{}, requirement]

    def register(self, api):
        self._auth.register(api)
        return self

    def requires(self, *scopes: str, roles=(), extractor=None) -> OptionalAuth:
        return OptionalAuth(
            self._auth.requires(*scopes, roles=roles, extractor=extractor)
        )

    async def __call__(self, req):
        return await self.authenticate(req)

    async def authenticate(self, req):
        auth = self._auth
        base = auth._auth if isinstance(auth, ScopedAuth) else auth
        if isinstance(base, AuthBase) and base._extract(req) is None:
            return None
        return await auth.authenticate(req)
