"""Core ASGI middleware shipped with Responder."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class HSTSMiddleware:
    """Emit a ``Strict-Transport-Security`` header on every response.

    Browsers ignore the header when it arrives over plain HTTP (RFC 6797), so it
    is safe to send unconditionally; it takes effect once the client is on HTTPS.
    Installed automatically by ``API(enable_hsts=True)`` alongside the HTTP→HTTPS
    redirect; add it directly via ``add_middleware`` to customise ``max_age``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_age: int = 31536000,
        include_subdomains: bool = True,
        preload: bool = False,
    ) -> None:
        self.app = app
        value = f"max-age={max_age}"
        if include_subdomains:
            value += "; includeSubDomains"
        if preload:
            value += "; preload"
        self.value = value

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_hsts(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("strict-transport-security", self.value)
            await send(message)

        await self.app(scope, receive, send_with_hsts)


class SecurityHeadersMiddleware:
    """Add common security headers to every response (opt-in).

    Sends ``X-Content-Type-Options: nosniff``, ``X-Frame-Options: DENY``, and
    ``Referrer-Policy: strict-origin-when-cross-origin`` by default. Pass
    ``content_security_policy`` / ``permissions_policy`` to add those, and
    ``headers=`` to override or add any others. A header a handler already set
    is left untouched. Enable via ``API(security_headers=True)`` or install
    directly with ``add_middleware``.
    """

    DEFAULTS = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin-when-cross-origin",
    }

    def __init__(
        self,
        app: ASGIApp,
        *,
        content_security_policy: str | None = None,
        permissions_policy: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.app = app
        resolved = dict(self.DEFAULTS)
        if content_security_policy:
            resolved["content-security-policy"] = content_security_policy
        if permissions_policy:
            resolved["permissions-policy"] = permissions_policy
        for key, value in (headers or {}).items():
            resolved[key.lower()] = value
        self.headers = list(resolved.items())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for key, value in self.headers:
                    response_headers.setdefault(key, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
