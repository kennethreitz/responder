"""Core ASGI middleware shipped with Responder."""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Callable
from typing import Any

import anyio
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# One Forwarded/X-Forwarded-For parser (and one repeated-line policy) for the
# whole package: the proxy middleware, rate limiting, and access logging must
# never disagree on the client IP for the same request.
from .util.net import _forwarded_element, _forwarded_for_ip, combined_header_getter

# A sync ``@api.middleware("http")`` runs its body on a worker thread and then
# *blocks* that thread waiting for the downstream (async) ``call_next`` to
# finish. If it borrowed a token from anyio's shared threadpool limiter (40 by
# default) it would hold that token for the whole request while the downstream
# sync view/hook/dependency needs a *second* token from the same pool — so
# enough concurrent requests would exhaust the pool and deadlock the server.
# We give middleware bodies their own effectively-unbounded limiter, decoupled
# from the shared pool that runs downstream sync work.
_MIDDLEWARE_THREAD_LIMITER = anyio.CapacityLimiter(math.inf)


class FunctionMiddleware(BaseHTTPMiddleware):
    """Adapt a ``(request, call_next)`` function to ASGI middleware.

    Powers the ``@api.middleware("http")`` decorator. An ``async def``
    function is used as the dispatch directly; a plain ``def`` function is
    offloaded to the threadpool (like sync views) and receives a *blocking*
    ``call_next`` that schedules the downstream call on the event loop and
    waits for the response. Non-HTTP traffic (WebSockets, lifespan) passes
    through untouched.

    Usually registered via the decorator, but it can also be installed
    directly: ``api.add_middleware(FunctionMiddleware, func=my_func)``.
    """

    def __init__(self, app: ASGIApp, func: Callable[..., Any]) -> None:
        if inspect.iscoroutinefunction(func):
            dispatch = func
        else:

            async def dispatch(request: Any, call_next: Any) -> Any:
                loop = asyncio.get_running_loop()

                def blocking_call_next(req: Any = request) -> Any:
                    return asyncio.run_coroutine_threadsafe(
                        call_next(req), loop
                    ).result()

                # Run outside the shared threadpool limiter so blocking on
                # ``call_next`` cannot starve downstream sync work of tokens.
                return await anyio.to_thread.run_sync(
                    func,
                    request,
                    blocking_call_next,
                    limiter=_MIDDLEWARE_THREAD_LIMITER,
                )

        super().__init__(app, dispatch=dispatch)


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
    ``headers=`` to override or add any others. A ``None`` value in
    ``headers=`` means "omit this header" — use it to drop a default (e.g.
    ``headers={"x-frame-options": None}`` for an embeddable app). A header a
    handler already set is left untouched. Enable via
    ``API(security_headers=True)`` or install directly with ``add_middleware``.
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
        headers: dict[str, str | None] | None = None,
    ) -> None:
        self.app = app
        resolved = dict(self.DEFAULTS)
        if content_security_policy:
            resolved["content-security-policy"] = content_security_policy
        if permissions_policy:
            resolved["permissions-policy"] = permissions_policy
        for key, value in (headers or {}).items():
            if value is None:
                # ``None`` means "omit this header" — drop the default rather
                # than crashing when Starlette tries to encode a None value.
                resolved.pop(key.lower(), None)
            else:
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


def _split_host_port(host: str, default_port: int) -> tuple[str, int]:
    """Split a ``Host``-header value into ``(name, port)``.

    Handles ``example.com``, ``example.com:8443``, and the bracketed IPv6
    forms ``[2001:db8::1]`` / ``[2001:db8::1]:8443`` (brackets stripped).
    Malformed values fall back to the whole string with the default port.
    """
    if host.startswith("["):
        end = host.find("]")
        if end > 1:
            name = host[1:end]
            rest = host[end + 1 :]
            if rest.startswith(":") and rest[1:].isdigit():
                return name, int(rest[1:])
            return name, default_port
        return host, default_port
    name, _, port = host.rpartition(":")
    if name and ":" not in name and port.isdigit():
        return name, int(port)
    return host, default_port


class ProxyHeadersMiddleware:
    """Rewrite the connection scope from a trusted reverse proxy's headers.

    Honors RFC 7239 ``Forwarded`` (its first, closest-to-client element) with
    fallback to the de-facto ``X-Forwarded-Proto``, ``X-Forwarded-Host``, and
    ``X-Forwarded-For``/``X-Real-IP`` headers, so that ``scope["scheme"]``,
    the ``Host`` header (and ``scope["server"]``), and ``scope["client"]``
    reflect the original client request. That makes redirects, URL building,
    HTTPS detection, and logged/rate-limited client IPs correct behind
    nginx, Caddy, or a load balancer.

    The immediate peer is trusted unconditionally: only install this (via
    ``API(trust_proxy_headers=True)``) when every request reaches Responder
    through a proxy you control that overwrites inbound forwarding headers —
    otherwise any client can spoof its scheme, host, and address.
    """

    _SCHEMES = {"http", "https"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or []
        get_header = combined_header_getter(raw_headers)

        forwarded = _forwarded_element(get_header("forwarded") or "")

        proto = forwarded.get("proto") or get_header("x-forwarded-proto") or ""
        proto = proto.split(",", 1)[0].strip().lower()
        host = forwarded.get("host") or get_header("x-forwarded-host") or ""
        host = host.split(",", 1)[0].strip()
        client_ip = None
        if "for" in forwarded:
            client_ip = _forwarded_for_ip(forwarded["for"])
        if client_ip is None:
            xff = (get_header("x-forwarded-for") or "").split(",", 1)[0].strip()
            client_ip = xff or ((get_header("x-real-ip") or "").strip() or None)

        if not (proto in self._SCHEMES or host or client_ip):
            await self.app(scope, receive, send)
            return

        scope = dict(scope)
        if proto in self._SCHEMES:
            if scope["type"] == "websocket":
                scope["scheme"] = "wss" if proto == "https" else "ws"
            else:
                scope["scheme"] = proto
        if host:
            encoded = host.encode("latin-1")
            scope["headers"] = [
                (k, v) for k, v in raw_headers if k.lower() != b"host"
            ] + [(b"host", encoded)]
            scope["server"] = _split_host_port(
                host, 443 if scope["scheme"] in ("https", "wss") else 80
            )
        if client_ip:
            original = scope.get("client")
            scope["client"] = (client_ip, original[1] if original else 0)

        await self.app(scope, receive, send)
