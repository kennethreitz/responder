"""Client IP resolution shared by rate limiting and access logging."""

from __future__ import annotations

from typing import Callable

__all__ = ["resolve_client_ip"]


def resolve_client_ip(
    client: tuple[str, int] | None,
    get_header: Callable[[str], str | None],
    *,
    trust_proxy_headers: bool = False,
) -> str | None:
    """Resolve the real client IP for a request.

    :param client: The ASGI ``scope["client"]`` tuple (host, port), or ``None``.
    :param get_header: ``name -> value`` case-insensitive header lookup.
    :param trust_proxy_headers: If ``True``, prefer ``X-Forwarded-For`` (its
        first, left-most entry) or ``X-Real-IP`` over the transport peer.
        Only enable this when Responder sits behind a reverse proxy that sets
        these headers itself — otherwise any client can spoof its own
        address and evade rate limits or pollute access logs. Off by default,
        in which case ``client`` (the actual TCP peer) is always used: behind
        an untrusted or misconfigured proxy that's the proxy's own address,
        but that's safer than trusting a client-supplied header blindly.
    """
    if trust_proxy_headers:
        forwarded = get_header("x-forwarded-for")
        if forwarded:
            ip = forwarded.split(",", 1)[0].strip()
            if ip:
                return ip
        real_ip = get_header("x-real-ip")
        if real_ip and real_ip.strip():
            return real_ip.strip()
    if client:
        return client[0]
    return None
