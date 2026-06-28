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
