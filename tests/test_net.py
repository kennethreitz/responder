"""Tests for client IP resolution shared by rate limiting and access logging."""

from responder.util.net import resolve_client_ip


def _headers(d):
    return lambda name: d.get(name.lower())


def test_uses_transport_peer_by_default():
    ip = resolve_client_ip(
        ("203.0.113.5", 1234), _headers({"x-forwarded-for": "1.2.3.4"})
    )
    assert ip == "203.0.113.5"


def test_ignores_forwarded_headers_when_not_trusted():
    ip = resolve_client_ip(
        ("10.0.0.1", 1234),
        _headers({"x-forwarded-for": "1.2.3.4"}),
        trust_proxy_headers=False,
    )
    assert ip == "10.0.0.1"


def test_trusts_forwarded_for_first_entry_when_enabled():
    ip = resolve_client_ip(
        ("10.0.0.1", 1234),  # the proxy's own peer address
        _headers({"x-forwarded-for": "1.2.3.4, 10.0.0.1"}),
        trust_proxy_headers=True,
    )
    assert ip == "1.2.3.4"


def test_trusts_real_ip_when_no_forwarded_for():
    ip = resolve_client_ip(
        ("10.0.0.1", 1234),
        _headers({"x-real-ip": "1.2.3.4"}),
        trust_proxy_headers=True,
    )
    assert ip == "1.2.3.4"


def test_falls_back_to_peer_when_trusted_headers_absent():
    ip = resolve_client_ip(
        ("10.0.0.1", 1234), _headers({}), trust_proxy_headers=True
    )
    assert ip == "10.0.0.1"


def test_returns_none_when_nothing_available():
    assert resolve_client_ip(None, _headers({})) is None
