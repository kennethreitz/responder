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
    "compare_digest",
]


async def _call(fn: Callable, *args: Any) -> Any:
    """Call ``fn`` (sync or async) with ``args``, awaiting as appropriate."""
    if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(
        getattr(fn, "__call__", None)  # noqa: B004 - inspecting __call__, not calling
    ):
        return await fn(*args)
    return await run_in_threadpool(fn, *args)


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
            value = req.params.get(self.name)
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
