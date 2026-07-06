"""Regression tests for bugs found in the post-v9.0.0 adversarial scan.

Each test pins a specific defect; see the matching commit for the fix.
"""

import responder


def _api(**kwargs):
    kwargs.setdefault("allowed_hosts", [";"])
    kwargs.setdefault("session_https_only", False)
    kwargs.setdefault("secret_key", "x" * 32)
    return responder.API(**kwargs)


def _url(s):
    return f"http://;{s}"


# --- CSRF: non-ASCII submitted token must 403, not 500 ----------------------


def test_csrf_non_ascii_token_is_403_not_500():
    api = _api(csrf=True)

    @api.route("/token")
    async def token(req, resp):
        resp.media = {"token": req.csrf_token}

    @api.route("/submit", methods=["POST"])
    async def submit(req, resp):
        resp.media = {"ok": True}

    client = api.requests
    client.get(_url("/token"))  # establish a session token

    # A non-ASCII token in the form field is fully client-controlled; it must
    # be rejected cleanly (403), not blow up hmac.compare_digest into a 500.
    r = client.post(_url("/submit"), data={"csrf_token": "häh"})
    assert r.status_code == 403

    # And a valid multipart csrf_token still passes (the fix must not break it).
    good = client.get(_url("/token")).json()["token"]
    r = client.post(_url("/submit"), data={"csrf_token": good})
    assert r.status_code == 200


# --- Non-ASCII "digits" must not crash int() parsing ------------------------


def test_split_host_port_non_ascii_digit_does_not_crash():
    from responder.middleware import _split_host_port

    # U+00B2 SUPERSCRIPT TWO: str.isdigit() is True, but int() raises. The
    # forwarded host arrives latin-1-decoded, so this byte is reachable.
    assert _split_host_port("example.com:8²", 80) == ("example.com:8²", 80)
    assert _split_host_port("[2001:db8::1]:8²", 443) == ("2001:db8::1", 443)
    # A real port still parses.
    assert _split_host_port("example.com:8443", 80) == ("example.com", 8443)


def test_proxy_non_ascii_forwarded_port_does_not_crash():
    api = _api(trust_proxy_headers=True, allowed_hosts=["*"])

    @api.route("/where")
    async def where(req, resp):
        resp.media = {"host": req.headers["Host"]}

    # ProxyHeadersMiddleware is outermost; an unhandled ValueError here would
    # send no response at all. The superscript-2 byte is valid latin-1 (0xB2).
    r = api.requests.get(
        _url("/where"),
        headers={
            "X-Forwarded-Proto": "https",
            # Raw latin-1 bytes: the test client refuses to ascii-encode a
            # non-ASCII header string, but a real proxy can send byte 0xB2.
            "X-Forwarded-Host": "example.com:8²".encode("latin-1"),
        },
    )
    assert r.status_code == 200


def test_non_ascii_content_length_is_not_500():
    api = _api()

    @api.route("/echo", methods=["POST"])
    async def echo(req, resp):
        resp.media = {"len": len(await req.content)}

    # A Content-Length of a superscript digit passes str.isdigit() but int()
    # would raise; the request must not 500.
    r = api.requests.post(
        _url("/echo"),
        content=b"hi",
        headers={"Content-Length": "²".encode("latin-1")},
    )
    assert r.status_code != 500


# --- Per-route CSRF override must not leak between routes --------------------


def test_csrf_reregistration_does_not_strip_protection():
    """Registering the same view a second time with csrf=False must not
    retroactively disable CSRF on its earlier, protected registration."""
    api = _api(csrf=True)

    async def handler(req, resp):
        resp.media = {"ok": True}

    api.add_route("/pay", handler, methods=["POST"])  # inherits app csrf=True
    api.route("/webhook", methods=["POST"], csrf=False)(handler)  # exempt alias

    # /pay must still demand a token; /webhook must not.
    assert api.requests.post(_url("/pay"), json={}).status_code == 403
    assert api.requests.post(_url("/webhook"), json={}).status_code == 200


def test_csrf_cbv_subclass_does_not_inherit_exemption():
    """A csrf=False exemption on a base CBV must not leak to a subclass
    registered normally under an app-wide csrf=True."""
    api = _api(csrf=True)

    @api.route("/webhook", csrf=False)
    class WebhookBase:
        async def on_post(self, req, resp):
            resp.media = {"ok": True}

    @api.route("/derived")
    class Derived(WebhookBase):
        pass

    assert api.requests.post(_url("/webhook"), json={}).status_code == 200
    assert api.requests.post(_url("/derived"), json={}).status_code == 403
