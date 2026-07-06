"""Regression tests for bugs found in the post-v9.0.0 adversarial scan.

Each test pins a specific defect; see the matching commit for the fix.
"""

import pytest

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


def test_csrf_true_on_websocket_route_is_rejected():
    """CSRF is an HTTP-form concept; the ws dispatch path never enforces it,
    so csrf=True on a ws route must fail loudly, not silently no-op."""
    api = _api(csrf=True)

    with pytest.raises(ValueError, match="[Ww]eb[Ss]ocket"):

        @api.route("/ws", websocket=True, csrf=True)
        async def ws(websocket):
            await websocket.accept()
            await websocket.close()

    # csrf=False on a ws route is a harmless explicit opt-out.
    @api.route("/ws-ok", websocket=True, csrf=False)
    async def ws_ok(websocket):
        await websocket.accept()
        await websocket.close()


# --- GraphQL: malformed request bodies must 400, not 500 --------------------


def _graphql_api():
    graphene = pytest.importorskip("graphene")
    from responder.ext.graphql import GraphQLView

    class Query(graphene.ObjectType):
        hello = graphene.String()

        def resolve_hello(self, info):
            return "hi"

    api = _api()
    api.add_route("/graph", GraphQLView(schema=graphene.Schema(query=Query), api=api))
    return api


def test_graphql_non_dict_json_body_is_400():
    api = _graphql_api()
    # A JSON array body contains "query" as an element; the membership check
    # used to pass and then subscripting a list raised TypeError -> 500.
    r = api.requests.post(_url("/graph"), json=["query"])
    assert r.status_code == 400


def test_graphql_multipart_without_query_is_400():
    api = _graphql_api()
    # Multipart parse consumes the streamed body; the old fallthrough to
    # req.text then raised RuntimeError -> 500.
    r = api.requests.post(_url("/graph"), files={"file": ("a.txt", b"data")})
    assert r.status_code == 400


# --- @limiter.limit must work on class-based-view methods --------------------


def test_ratelimit_limit_on_cbv_method():
    from responder.ext.ratelimit import RateLimiter

    api = _api()
    limiter = RateLimiter(requests=1, period=60)

    @api.route("/cbv")
    class View:
        @limiter.limit
        async def on_get(self, req, resp):
            resp.media = {"ok": True}

    client = api.requests
    # The bound method prepends self; the wrapper used to treat the view
    # instance as the request and 500 on every call.
    first = client.get(_url("/cbv"))
    assert first.status_code == 200
    assert first.json() == {"ok": True}
    # And the limiter still actually limits.
    assert client.get(_url("/cbv")).status_code == 429


def test_ratelimit_limit_on_sync_cbv_method():
    from responder.ext.ratelimit import RateLimiter

    api = _api()
    limiter = RateLimiter(requests=1, period=60)

    @api.route("/cbv-sync")
    class View:
        @limiter.limit
        def on_get(self, req, resp):
            resp.media = {"ok": True}

    client = api.requests
    assert client.get(_url("/cbv-sync")).status_code == 200
    assert client.get(_url("/cbv-sync")).status_code == 429
