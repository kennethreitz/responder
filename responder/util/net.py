"""Client IP resolution shared by rate limiting, access logging, and the
proxy-headers middleware — one parser, one precedence, everywhere."""

from __future__ import annotations

from typing import Callable

__all__ = ["combined_header_getter", "resolve_client_ip"]


def combined_header_getter(
    raw_headers: list[tuple[bytes, bytes]],
) -> Callable[[str], str | None]:
    """Build a ``name -> value`` lookup over raw ASGI headers that joins
    repeated lines with commas, in received order (RFC 9110 list semantics).

    HTTP allows ``Forwarded``/``X-Forwarded-For`` to arrive as several lines
    (each proxy hop may append its own); parsing "the first element" is only
    correct against the *combined* list. A plain ``dict(raw_headers)`` keeps
    just the last line and silently reverses that order.
    """
    combined: dict[bytes, str] = {}
    for key, value in raw_headers:
        name = key.lower()
        decoded = value.decode("latin-1")
        combined[name] = (
            f"{combined[name]}, {decoded}" if name in combined else decoded
        )

    def get_header(name: str) -> str | None:
        return combined.get(name.lower().encode("latin-1"))

    return get_header


def _forwarded_element(value: str) -> dict[str, str]:
    """Parse the first (closest-to-client) element of an RFC 7239
    ``Forwarded`` header into its lowercase parameter map."""
    params: dict[str, str] = {}
    for pair in value.split(",", 1)[0].split(";"):
        key, sep, val = pair.partition("=")
        if sep:
            params[key.strip().lower()] = val.strip().strip('"')
    return params


def _forwarded_for_ip(value: str) -> str | None:
    """Extract the IP from an RFC 7239 ``for=`` node identifier.

    Handles ``[ipv6]:port``, ``ip:port``, and bare forms; obfuscated
    (``_hidden``) and ``unknown`` identifiers yield ``None``.
    """
    value = value.strip()
    if not value or value.lower() == "unknown" or value.startswith("_"):
        return None
    if value.startswith("["):  # "[2001:db8::1]:443" or "[2001:db8::1]"
        end = value.find("]")
        return value[1:end] if end > 1 else None
    host, _, port = value.rpartition(":")
    # A lone colon-pair is host:port; multiple colons mean a bare IPv6.
    if host and ":" not in host and port.isdigit():
        return host
    return value


def resolve_client_ip(
    client: tuple[str, int] | None,
    get_header: Callable[[str], str | None],
    *,
    trust_proxy_headers: bool = False,
) -> str | None:
    """Resolve the real client IP for a request.

    :param client: The ASGI ``scope["client"]`` tuple (host, port), or ``None``.
    :param get_header: ``name -> value`` case-insensitive header lookup. For
        headers that legally repeat (``Forwarded``, ``X-Forwarded-For``) it
        must return every line joined with commas in received order — build
        it with :func:`combined_header_getter` (or join ``get_list`` values)
        so "first element" means the closest-to-client one, not whichever
        line a plain dict happened to keep.
    :param trust_proxy_headers: If ``True``, prefer the proxy's forwarding
        headers over the transport peer, in the same precedence
        :class:`~responder.middleware.ProxyHeadersMiddleware` uses to rewrite
        ``scope["client"]``: RFC 7239 ``Forwarded`` (its first,
        closest-to-client ``for=``), then ``X-Forwarded-For`` (first entry),
        then ``X-Real-IP``. Only enable this when Responder sits behind a
        reverse proxy that sets these headers itself — otherwise any client
        can spoof its own address and evade rate limits or pollute access
        logs. Off by default, in which case ``client`` (the actual TCP peer)
        is always used: behind an untrusted or misconfigured proxy that's the
        proxy's own address, but that's safer than trusting a client-supplied
        header blindly.
    """
    if trust_proxy_headers:
        forwarded = get_header("forwarded")
        if forwarded:
            node = _forwarded_element(forwarded).get("for")
            if node:
                ip = _forwarded_for_ip(node)
                if ip:
                    return ip
        xff = get_header("x-forwarded-for")
        if xff:
            ip = xff.split(",", 1)[0].strip()
            if ip:
                return ip
        real_ip = get_header("x-real-ip")
        if real_ip and real_ip.strip():
            return real_ip.strip()
    if client:
        return client[0]
    return None
