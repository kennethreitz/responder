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

For token-based APIs, :class:`JWTAuth` validates Bearer JWTs (signature,
``exp``/``nbf``/``iat``, audience, issuer, optional JWKS key discovery) and
injects the verified claims as the principal, and :class:`OAuth2Auth` documents
OAuth2 flows in OpenAPI (so Swagger UI's *Authorize* button works) while
enforcing bearer-token validation at runtime. Both require the optional PyJWT
dependency: ``pip install 'responder[jwt]'``.
"""

from __future__ import annotations

import base64
import binascii
import inspect
import threading
import time
from secrets import compare_digest
from typing import Any, Callable
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

__all__ = [
    "AuthBase",
    "AuthPolicy",
    "BearerAuth",
    "BasicAuth",
    "APIKeyAuth",
    "JWTAuth",
    "OAuth2Auth",
    "OAuth2Flow",
    "OAuth2AuthorizationCodeFlow",
    "OAuth2ClientCredentialsFlow",
    "OAuth2PasswordFlow",
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
    itself a (non-string) iterable of strings is treated as the scope set.
    Falls back to an empty set, so a principal that carries no scope information
    simply satisfies no scope requirement.

    The OAuth2/JWT ``scope`` claim (a space-delimited string) is deliberately
    *not* consulted here: :class:`JWTAuth`/:class:`OAuth2Auth` normalize their
    token's ``scope``/``scp`` claim into a ``scopes`` list on the principal, so
    the generic path only ever needs the ``scopes``/``roles`` keys — matching
    the behavior every non-JWT scheme relied on. When a principal carries both
    ``scopes`` and ``roles`` the two are unioned, so normalizing an OAuth2
    ``scope`` claim into ``scopes`` never masks a ``roles``-based grant.
    """
    found = False
    held: frozenset[str] = frozenset()
    for attr in ("scopes", "roles"):
        value = getattr(principal, attr, None)
        if value is None and isinstance(principal, dict):
            value = principal.get(attr)
        if value is not None:
            found = True
            held |= _scope_set(value)
    if found:
        return held
    if not isinstance(principal, str) and isinstance(
        principal, (list, tuple, set, frozenset)
    ):
        return frozenset(principal)
    return frozenset()


def _as_tuple(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _scope_set(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset(value.split())
    # A non-iterable (or otherwise unexpected) scope value carries no scopes,
    # rather than crashing the request with a 500. Mappings would iterate their
    # keys, which is never what a scope claim means, so exclude them too.
    if isinstance(value, dict):
        return frozenset()
    try:
        return frozenset(str(item) for item in value)
    except TypeError:
        return frozenset()


def _matches_any(value: str, candidates: list[str]) -> bool:
    """Constant-time membership test (checks every candidate, no short-circuit)."""
    value_b = value.encode()
    matched = False
    for candidate in candidates:
        if compare_digest(value_b, candidate.encode()):
            matched = True
    return matched


def _normalize_scope_claims(claims: Any) -> Any:
    """Fold an OAuth2/JWT ``scope``/``scp`` claim into a ``scopes`` list.

    The OAuth2 ``scope`` claim is a space-delimited string and ``scp`` is a
    common (Azure AD / Auth0) variant that may be a string or a list. Both are
    unioned into a ``scopes`` list on the claims dict so the generic scope
    extractor (:func:`_default_scopes`) — which only inspects ``scopes``/
    ``roles`` — sees them, without letting the raw ``scope`` claim shadow or
    reorder the existing extraction for non-JWT schemes.

    The caller's claims object is never mutated: a token introspection callback
    may return a cached/shared dict, and stamping ``scopes`` onto it would both
    leak state across requests and (via a short-circuit on a pre-existing
    ``scopes`` key) freeze a stale scope set even after the authorization server
    downscopes the token. A shallow copy is returned whenever ``scopes`` needs
    to be derived. An explicit ``scopes`` claim already present is honored, and
    is still unioned with any ``scope``/``scp`` grant rather than shadowing it.
    """
    if not isinstance(claims, dict):
        return claims
    if not any(key in claims for key in ("scope", "scp")):
        return claims
    granted = _scope_set(claims.get("scopes"))
    for key in ("scope", "scp"):
        if key in claims:
            granted |= _scope_set(claims[key])
    normalized = dict(claims)
    normalized["scopes"] = sorted(granted)
    return normalized


_LOCAL_JWKS_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _require_secure_jwks_url(url: str) -> None:
    """Reject a plaintext JWKS URL (MITM => auth bypass), allowing localhost.

    A JWKS fetch over ``http`` lets a network attacker serve their own signing
    keys and forge accepted tokens, so only ``https`` is permitted — except for
    the loopback hosts (``localhost`` / ``127.0.0.1`` / ``[::1]``) used by tests
    and local development, which are not exposed to a MITM.
    """
    parts = urlsplit(url)
    if parts.scheme == "https":
        return
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" and host in _LOCAL_JWKS_HOSTS:
        return
    raise ValueError(
        "jwks_url must use https (a plaintext JWKS fetch can be MITM'd into an "
        "auth bypass); pass allow_insecure_jwks=True to override for a trusted "
        f"non-loopback endpoint, got: {url!r}"
    )


def _require_pyjwt():
    """Import and return PyJWT, with a helpful error when it is missing."""
    try:
        import jwt
    except ImportError as exc:
        raise ImportError(
            "PyJWT is required for JWT support: pip install 'responder[jwt]'"
        ) from exc
    return jwt


def _scopes_map(scopes: Any) -> dict[str, str]:
    """Normalize OAuth2 scope declarations to the OpenAPI ``{name: description}``
    map, accepting a mapping, an iterable of names, or a space-delimited string.
    """
    if scopes is None:
        return {}
    if isinstance(scopes, dict):
        return {str(name): str(description) for name, description in scopes.items()}
    if isinstance(scopes, str):
        return dict.fromkeys(scopes.split(), "")
    return {str(name): "" for name in scopes}


def _challenge_with_params(challenge: str, **params: str) -> str:
    separator = ", " if " " in challenge else " "
    additions = ", ".join(f'{key}="{value}"' for key, value in params.items())
    return f"{challenge}{separator}{additions}"


class _ValueEqual:
    """Value equality for auth helpers: same concrete type, equal fields.

    Auth helpers are plain configuration objects, so two independently
    constructed instances with the same settings are interchangeable. This
    matters when the same ``Router`` is included at several prefixes with
    fresh-but-identical auth objects — the re-inclusion guard must not treat
    them as conflicting. The hash is type-based (config objects are mutable),
    which keeps instances usable as dict/set keys while staying consistent
    with ``__eq__``.
    """

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return other.__dict__ == self.__dict__

    def __hash__(self) -> int:
        return hash(type(self))


class AuthBase(_ValueEqual):
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

    def requires(
        self,
        *scopes: str,
        roles: Any = (),
        extractor: Callable | None = None,
    ) -> ScopedAuth:
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

    def _has_credential(self, req: Any) -> bool:
        return self._extract(req) is not None

    async def _verify(self, credential):
        raise NotImplementedError

    def _challenge(self):
        return None

    def security_scheme(self) -> dict:
        raise NotImplementedError


class AuthPolicy(_ValueEqual):
    """A named auth policy for reusing route auth intent.

    ``AuthPolicy`` wraps any existing auth helper without changing how the
    underlying scheme authenticates or appears in OpenAPI. The name is an
    application-facing label, useful for keeping route declarations readable::

        admin = api.policy("admin", bearer.requires("admin"))

        @api.get("/admin", auth=admin)
        def dashboard(req, resp, *, user): ...
    """

    def __init__(self, name: str, auth: Any):
        if not name:
            raise ValueError("AuthPolicy requires a non-empty name")
        if auth is None:
            raise ValueError("AuthPolicy requires an auth helper")
        self.name = str(name)
        self._auth = auth

    @property
    def optional_auth(self) -> bool:
        return bool(getattr(self._auth, "optional_auth", False))

    @property
    def scheme_name(self) -> str:
        return getattr(self._auth, "scheme_name", self.name)

    @property
    def auto_error(self) -> bool:
        return getattr(self._auth, "auto_error", True)

    def security_scheme(self) -> dict | None:
        if not hasattr(self._auth, "security_scheme"):
            return None
        return self._auth.security_scheme()

    def security_requirement(self):
        if hasattr(self._auth, "security_requirement"):
            return self._auth.security_requirement()
        if hasattr(self._auth, "scheme_name"):
            return {self.scheme_name: []}
        return None

    def register(self, api):
        if hasattr(self._auth, "register"):
            self._auth.register(api)
        else:
            scheme = self.security_scheme()
            if scheme is None:
                raise ValueError(
                    f"Auth policy {self.name!r} has no OpenAPI security scheme"
                )
            api.add_security_scheme(self.scheme_name, scheme)
        return self

    def requires(
        self,
        *scopes: str,
        roles: Any = (),
        extractor: Callable | None = None,
    ) -> AuthPolicy:
        if not hasattr(self._auth, "requires"):
            raise TypeError(
                f"Auth policy {self.name!r} does not support scoped requirements"
            )
        return AuthPolicy(
            self.name,
            self._auth.requires(*scopes, roles=roles, extractor=extractor),
        )

    def optional(self) -> AuthPolicy:
        if not hasattr(self._auth, "optional"):
            raise TypeError(f"Auth policy {self.name!r} does not support optional auth")
        return AuthPolicy(self.name, self._auth.optional())

    async def __call__(self, req):
        return await self.authenticate(req)

    async def authenticate(self, req):
        if hasattr(self._auth, "authenticate"):
            return await self._auth.authenticate(req)
        return await _call(self._auth, req)

    def __repr__(self) -> str:
        return f"<AuthPolicy {self.name!r} auth={self._auth!r}>"


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

    def _has_credential(self, req: Any) -> bool:
        return bool(req.headers.get("Authorization"))

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

    def _has_credential(self, req: Any) -> bool:
        return bool(req.headers.get("Authorization"))

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


class JWTAuth(AuthBase):
    """``Authorization: Bearer <JWT>`` authentication with signature validation.

    Validates the token's signature (HS256 by default; RS256/ES256 and friends
    when the ``cryptography`` package is installed), its time claims
    (``exp``/``nbf``/``iat``, with optional ``leeway``), and — when configured —
    its ``aud`` and ``iss`` claims. The decoded claims dict becomes the
    principal, so ``scope``/``scopes``/``roles`` claims feed straight into
    :meth:`AuthBase.requires` scope checks::

        from responder.ext.auth import JWTAuth

        auth = JWTAuth(
            secret="s3cret",             # or jwks_url="https://issuer/.../jwks.json"
            audience="https://api.example.com",
            issuer="https://issuer.example.com",
        )

        @api.get("/me", auth=auth)
        async def me(req, resp, *, user):        # user == the claims dict
            resp.media = {"sub": user["sub"]}

        @api.get("/admin", auth=auth.requires("admin"))
        async def admin(req, resp, *, user): ...  # 403 without the scope

    Invalid, expired, or missing tokens reject with ``401`` and a ``Bearer``
    challenge; insufficient scopes (via ``requires``) reject with ``403``.

    Keys come from either a static ``secret`` (the HMAC secret for ``HS*``
    algorithms, or a PEM public key for ``RS*``/``ES*``) or a ``jwks_url``,
    which resolves the signing key by the token's ``kid`` header via PyJWT's
    JWKS client — key sets are cached for ``jwks_cache_ttl`` seconds and
    refreshed when an unknown ``kid`` appears (key rotation). Because the
    ``kid`` is attacker-controlled and read before any signature check, an
    unknown ``kid`` only triggers a network refresh at most once per short
    cooldown window; further unknown ``kid`` lookups inside that window reject
    with ``401`` without an upstream fetch, so a flood of random ``kid`` values
    cannot amplify into unbounded JWKS refetches. A plaintext (``http``)
    ``jwks_url`` is rejected unless it targets loopback or ``allow_insecure_jwks``
    is set, since a MITM on the fetch would be a full auth bypass.

    Requires the optional PyJWT dependency (``pip install 'responder[jwt]'``);
    JWKS and asymmetric algorithms additionally need ``cryptography``.

    :param secret: HMAC secret or PEM public key used to verify signatures.
    :param jwks_url: JWKS endpoint to fetch signing keys from (alternative to
                     ``secret``; exactly one of the two must be given).
    :param algorithms: Allowed signature algorithms (default ``("HS256",)``).
    :param audience: Expected ``aud`` claim; unchecked when ``None``.
    :param issuer: Expected ``iss`` claim; unchecked when ``None``.
    :param leeway: Clock-skew allowance in seconds for time-claim validation.
    :param options: Extra PyJWT decode options (e.g. ``{"require": ["exp"]}``).
    :param verify: Optional sync/async callback receiving the validated claims
                   dict; return the principal to inject, or a falsy value to
                   reject with ``401``. Defaults to the claims dict itself.
    :param realm: Optional realm included in the ``WWW-Authenticate`` challenge.
    :param jwks_cache_ttl: JWKS cache lifetime in seconds (default 300).
    :param require_exp: Require an ``exp`` claim (default ``True``, secure).
                        PyJWT does not require ``exp`` on its own, so an
                        expiry-less token would otherwise be accepted forever;
                        pass ``False`` to accept tokens without ``exp``.
    :param allow_insecure_jwks: Permit a non-``https`` ``jwks_url`` (default
                        ``False``). A plaintext JWKS fetch is trivially
                        MITM-able into a full auth bypass, so only ``https`` (or
                        ``http://localhost`` / ``127.0.0.1`` / ``[::1]`` for
                        local development) is accepted unless this is set.
    """

    scheme_name = "jwtAuth"

    #: Repeated lookups of an unknown ``kid`` within this many seconds are
    #: refused without hitting the network again, bounding the JWKS-refetch
    #: amplification an attacker can drive with random ``kid`` values.
    _jwks_miss_cooldown = 10.0

    def __init__(
        self,
        secret=None,
        *,
        jwks_url=None,
        algorithms=("HS256",),
        audience=None,
        issuer=None,
        leeway=0,
        options=None,
        verify=None,
        realm=None,
        jwks_cache_ttl=300,
        require_exp=True,
        allow_insecure_jwks=False,
        auto_error=True,
        scheme_name=None,
    ):
        super().__init__(verify, auto_error=auto_error, scheme_name=scheme_name)
        if secret is None and jwks_url is None:
            raise ValueError("JWTAuth requires secret= or jwks_url=")
        if secret is not None and jwks_url is not None:
            raise ValueError("JWTAuth accepts secret= or jwks_url=, not both")
        if jwks_url is not None and not allow_insecure_jwks:
            _require_secure_jwks_url(jwks_url)
        self.secret = secret
        self.jwks_url = jwks_url
        self.algorithms = (
            [algorithms] if isinstance(algorithms, str) else list(algorithms)
        )
        self.audience = audience
        self.issuer = issuer
        self.leeway = leeway
        self.options = dict(options) if options else {}
        self.realm = realm
        self.jwks_cache_ttl = jwks_cache_ttl
        self.require_exp = require_exp
        self.allow_insecure_jwks = allow_insecure_jwks
        self._jwks_client = None  # built lazily; excluded from value equality
        # JWKS state is shared across threadpool workers, so guard it: the
        # client (a cache of resolved signing keys) and the negative cache that
        # rate-limits unknown-``kid`` refetches both need a lock.
        self._jwks_lock = threading.Lock()
        self._jwks_last_miss = 0.0

    # Fresh-but-identical instances must stay interchangeable (see _ValueEqual)
    # even after one of them has lazily built its JWKS client, so equality
    # compares configuration only.
    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self._config() == other._config()

    __hash__ = _ValueEqual.__hash__

    _transient_attrs = frozenset(
        {"_jwks_client", "_jwks_lock", "_jwks_last_miss"}
    )

    def _config(self) -> dict:
        return {
            k: v for k, v in self.__dict__.items() if k not in self._transient_attrs
        }

    # Token extraction is plain RFC 6750 bearer extraction.
    _extract = BearerAuth._extract
    _has_credential = BearerAuth._has_credential

    def _jwks_client_locked(self):
        """Return the (lazily built, caching) JWKS client; call under the lock."""
        if self._jwks_client is None:
            jwt = _require_pyjwt()
            # cache_keys keeps resolved signing keys so a known ``kid`` never
            # refetches; max_cached_keys bounds memory against key churn.
            self._jwks_client = jwt.PyJWKClient(
                self.jwks_url,
                cache_keys=True,
                max_cached_keys=16,
                lifespan=int(self.jwks_cache_ttl),
            )
        return self._jwks_client

    def _signing_key(self, token):
        """Resolve the verification key for ``token`` (static or via JWKS).

        Runs in a threadpool worker, so all JWKS state is touched under
        ``_jwks_lock``. The ``kid`` is attacker-controlled and is read before
        any signature check, so an unknown ``kid`` must not be allowed to drive
        an unbounded number of blocking JWKS refetches: a cached key set is
        consulted first, and a network refresh for a missing ``kid`` is
        rate-limited to at most once per ``_jwks_miss_cooldown`` window. Misses
        inside the cooldown raise ``PyJWKClientError`` (-> 401) without I/O.
        """
        if self.jwks_url is None:
            return self.secret
        jwt = _require_pyjwt()
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        with self._jwks_lock:
            client = self._jwks_client_locked()
            # First try whatever key set is already cached — no network I/O.
            signing_key = client.match_kid(client.get_signing_keys(), kid)
            if signing_key is not None:
                return signing_key.key
            # Unknown kid: only refetch if we are outside the cooldown, so a
            # flood of random kids cannot amplify into a refetch per request.
            now = time.monotonic()
            if now - self._jwks_last_miss < self._jwks_miss_cooldown:
                raise jwt.PyJWKClientError(
                    f'Unable to find a signing key that matches: "{kid}"'
                )
            self._jwks_last_miss = now
            signing_key = client.match_kid(
                client.get_signing_keys(refresh=True), kid
            )
            if signing_key is None:
                raise jwt.PyJWKClientError(
                    f'Unable to find a signing key that matches: "{kid}"'
                )
            return signing_key.key

    def _decode_options(self) -> dict[str, Any]:
        """Build the PyJWT ``decode`` options for this configuration.

        Merges the user-supplied ``options`` with two secure defaults that
        PyJWT does not apply on its own:

        * ``verify_aud=False`` when no ``audience`` is configured — otherwise
          PyJWT still enforces ``aud`` and rejects every token that carries an
          ``aud`` claim (i.e. virtually all real OIDC access tokens), even
          though this scheme documents ``aud`` as unchecked when unset.
        * ``exp`` added to the ``require`` list when ``require_exp`` is set,
          so an expiry-less token is not accepted forever.

        A user-supplied ``options`` value always wins, so an explicit
        ``verify_aud`` / ``require`` is never clobbered.
        """
        options: dict[str, Any] = dict(self.options)
        if self.require_exp:
            required = list(options.get("require", []))
            if "exp" not in required:
                required.append("exp")
            options["require"] = required
        if self.audience is None:
            options.setdefault("verify_aud", False)
        return options

    async def _verify(self, token):
        jwt = _require_pyjwt()
        try:
            # Resolving the JWKS signing key does blocking urllib I/O (an
            # unknown ``kid`` triggers a network refetch with a multi-second
            # timeout), so run it off the event loop — otherwise an attacker
            # sending random ``kid`` values stalls every concurrent request.
            key = await run_in_threadpool(self._signing_key, token)
            claims = jwt.decode(
                token,
                key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options=self._decode_options(),
            )
        except (jwt.PyJWTError, TypeError, ValueError):
            # Bad signature, expired, wrong aud/iss, malformed, disallowed or
            # mismatched algorithm (e.g. an HS256 token against an RSA key),
            # unknown kid, unreachable JWKS, ... — all reject with 401. A
            # JWKS-resolved public *key* fed to an HMAC algorithm makes PyJWT's
            # key-prep raise a bare TypeError/ValueError (not a PyJWTError), so
            # those are treated as an invalid token too rather than a 500.
            return None
        claims = _normalize_scope_claims(claims)
        if self.verify is not None:
            return await _call(self.verify, claims)
        return claims

    def _challenge(self):
        return f'Bearer realm="{self.realm}"' if self.realm else "Bearer"

    def security_scheme(self):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}


class OAuth2Flow(_ValueEqual):
    """Base description of a single OAuth2 flow for OpenAPI ``securitySchemes``.

    Subclasses set :attr:`flow_name` (the OpenAPI flows-object key) and collect
    the endpoint URLs the flow needs. ``scopes`` may be a ``{name: description}``
    mapping, an iterable of names, or a space-delimited string.
    """

    flow_name: str = ""

    def __init__(
        self,
        *,
        authorization_url=None,
        token_url=None,
        refresh_url=None,
        scopes=None,
    ):
        self.authorization_url = authorization_url
        self.token_url = token_url
        self.refresh_url = refresh_url
        self.scopes = _scopes_map(scopes)

    @staticmethod
    def _require_url(name: str, value: Any) -> str:
        """Reject an empty/blank required flow URL at construction.

        ``spec()`` drops falsy URLs, so an empty ``authorizationUrl``/
        ``tokenUrl`` would silently emit a ``securityScheme`` missing a field
        OpenAPI marks required, which validators reject. Fail fast instead.
        """
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"OAuth2 flow requires a non-empty {name}")
        return value

    def spec(self) -> dict:
        """The OpenAPI flow object (``authorizationUrl``/``tokenUrl``/...)."""
        flow: dict[str, Any] = {}
        if self.authorization_url:
            flow["authorizationUrl"] = self.authorization_url
        if self.token_url:
            flow["tokenUrl"] = self.token_url
        if self.refresh_url:
            flow["refreshUrl"] = self.refresh_url
        flow["scopes"] = dict(self.scopes)
        return flow

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.spec()!r}>"


class OAuth2AuthorizationCodeFlow(OAuth2Flow):
    """The OAuth2 authorization-code flow (interactive user login)."""

    flow_name = "authorizationCode"

    def __init__(self, authorization_url, token_url, *, refresh_url=None, scopes=None):
        super().__init__(
            authorization_url=self._require_url(
                "authorizationUrl", authorization_url
            ),
            token_url=self._require_url("tokenUrl", token_url),
            refresh_url=refresh_url,
            scopes=scopes,
        )


class OAuth2ClientCredentialsFlow(OAuth2Flow):
    """The OAuth2 client-credentials flow (machine-to-machine)."""

    flow_name = "clientCredentials"

    def __init__(self, token_url, *, refresh_url=None, scopes=None):
        super().__init__(
            token_url=self._require_url("tokenUrl", token_url),
            refresh_url=refresh_url,
            scopes=scopes,
        )


class OAuth2PasswordFlow(OAuth2Flow):
    """The OAuth2 resource-owner-password flow (username/password for a token)."""

    flow_name = "password"

    def __init__(self, token_url, *, refresh_url=None, scopes=None):
        super().__init__(
            token_url=self._require_url("tokenUrl", token_url),
            refresh_url=refresh_url,
            scopes=scopes,
        )


class OAuth2Auth(AuthBase):
    """OAuth2 bearer authentication: documented flows, validated bearer tokens.

    In OpenAPI this emits a ``type: oauth2`` security scheme with the declared
    flows and scopes, which lights up Swagger UI's *Authorize* button for
    authorization-code, client-credentials, and password flows. At runtime it
    is a resource server, not an authorization server: it extracts the
    ``Authorization: Bearer`` token and validates it — either through a
    :class:`JWTAuth` (``jwt=``) or a sync/async introspection callable
    (``verify=``) that receives the token and returns the principal (or a
    falsy value to reject)::

        from responder.ext.auth import JWTAuth, OAuth2Auth

        oauth2 = OAuth2Auth.authorization_code(
            "https://issuer.example.com/authorize",
            "https://issuer.example.com/oauth/token",
            scopes={"read": "Read access", "write": "Write access"},
            jwt=JWTAuth(jwks_url="https://issuer.example.com/.well-known/jwks.json",
                        algorithms=("RS256",),
                        audience="https://api.example.com"),
        )

        @api.get("/items", auth=oauth2.requires("read"))
        async def items(req, resp, *, user): ...

    ``401`` (missing/invalid token) and ``403`` (missing scopes, via
    ``requires``) semantics match the other schemes.

    :param flows: One flow description or an iterable of them (see
                  :class:`OAuth2AuthorizationCodeFlow`,
                  :class:`OAuth2ClientCredentialsFlow`,
                  :class:`OAuth2PasswordFlow`).
    :param jwt: A :class:`JWTAuth` that validates the bearer token locally.
    :param verify: Token-introspection callable (alternative to ``jwt``;
                   exactly one of the two must be given).
    :param description: Optional description shown in the OpenAPI scheme.
    :param realm: Optional realm included in the ``WWW-Authenticate`` challenge.
    """

    scheme_name = "oauth2Auth"

    def __init__(
        self,
        flows,
        *,
        jwt=None,
        verify=None,
        description=None,
        realm=None,
        auto_error=True,
        scheme_name=None,
    ):
        super().__init__(verify, auto_error=auto_error, scheme_name=scheme_name)
        self.flows = tuple(flows) if isinstance(flows, (list, tuple)) else (flows,)
        if not self.flows:
            raise ValueError("OAuth2Auth requires at least one flow")
        for flow in self.flows:
            if not isinstance(flow, OAuth2Flow):
                raise TypeError(
                    "OAuth2Auth flows must be OAuth2Flow instances, "
                    f"got {type(flow).__name__}"
                )
        names = [flow.flow_name for flow in self.flows]
        if len(set(names)) != len(names):
            raise ValueError("OAuth2Auth flows must have distinct flow types")
        if jwt is None and verify is None:
            raise ValueError("OAuth2Auth requires jwt= or verify=")
        if jwt is not None and verify is not None:
            raise ValueError("OAuth2Auth accepts jwt= or verify=, not both")
        self.jwt = jwt
        self.description = description
        self.realm = realm

    @classmethod
    def authorization_code(
        cls,
        authorization_url: str,
        token_url: str,
        *,
        refresh_url: str | None = None,
        scopes: Any = None,
        **kwargs: Any,
    ) -> OAuth2Auth:
        """An :class:`OAuth2Auth` documenting a single authorization-code flow."""
        flow = OAuth2AuthorizationCodeFlow(
            authorization_url, token_url, refresh_url=refresh_url, scopes=scopes
        )
        return cls(flow, **kwargs)

    @classmethod
    def client_credentials(
        cls,
        token_url: str,
        *,
        refresh_url: str | None = None,
        scopes: Any = None,
        **kwargs: Any,
    ) -> OAuth2Auth:
        """An :class:`OAuth2Auth` documenting a single client-credentials flow."""
        flow = OAuth2ClientCredentialsFlow(
            token_url, refresh_url=refresh_url, scopes=scopes
        )
        return cls(flow, **kwargs)

    @classmethod
    def password(
        cls,
        token_url: str,
        *,
        refresh_url: str | None = None,
        scopes: Any = None,
        **kwargs: Any,
    ) -> OAuth2Auth:
        """An :class:`OAuth2Auth` documenting a single password flow."""
        flow = OAuth2PasswordFlow(token_url, refresh_url=refresh_url, scopes=scopes)
        return cls(flow, **kwargs)

    # Bearer-token extraction, same as BearerAuth/JWTAuth.
    _extract = BearerAuth._extract
    _has_credential = BearerAuth._has_credential

    async def _verify(self, token):
        if self.jwt is not None:
            return await self.jwt._verify(token)
        # An introspection callback returns an OAuth2 principal that may carry
        # the space-delimited ``scope`` claim; normalize it into ``scopes`` so
        # the generic extractor sees it (JWTAuth does the same for its claims).
        return _normalize_scope_claims(await _call(self.verify, token))

    def _challenge(self):
        return f'Bearer realm="{self.realm}"' if self.realm else "Bearer"

    def security_scheme(self):
        scheme: dict[str, Any] = {
            "type": "oauth2",
            "flows": {flow.flow_name: flow.spec() for flow in self.flows},
        }
        if self.description:
            scheme["description"] = self.description
        return scheme


class ScopedAuth(_ValueEqual):
    """An auth scheme wrapped with a scope/role requirement.

    Created via :meth:`AuthBase.requires`. It authenticates through the wrapped
    scheme, then enforces that the resulting principal holds every required
    scope, rejecting with ``403`` otherwise. It proxies ``scheme_name``,
    ``security_scheme()``, and ``register()`` so it documents and registers the
    same OpenAPI security scheme as the scheme it wraps — the required scopes
    surface as the operation's security-requirement value.
    """

    def __init__(
        self,
        auth: AuthBase,
        scopes: Any = (),
        *,
        roles: Any = (),
        extractor: Callable | None = None,
    ) -> None:
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

    def security_scheme(self) -> dict | None:
        return self._auth.security_scheme()

    def security_requirement(self) -> dict:
        return {self.scheme_name: list(self.required_scopes)}

    def register(self, api):
        self._auth.register(api)
        return self

    def requires(
        self,
        *scopes: str,
        roles: Any = (),
        extractor: Callable | None = None,
    ) -> ScopedAuth:
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
                    "WWW-Authenticate": _challenge_with_params(
                        challenge,
                        error="insufficient_scope",
                        scope=scope,
                    )
                }
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient scope: {' '.join(missing)}",
                headers=headers,
            )
        return principal


class OptionalAuth(_ValueEqual):
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

    def security_scheme(self) -> dict | None:
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

    def requires(
        self,
        *scopes: str,
        roles: Any = (),
        extractor: Callable | None = None,
    ) -> OptionalAuth:
        return OptionalAuth(
            self._auth.requires(*scopes, roles=roles, extractor=extractor)
        )

    async def __call__(self, req):
        return await self.authenticate(req)

    async def authenticate(self, req):
        auth = self._auth
        base = auth._auth if isinstance(auth, ScopedAuth) else auth
        if isinstance(base, AuthBase) and not base._has_credential(req):
            return None
        return await auth.authenticate(req)
