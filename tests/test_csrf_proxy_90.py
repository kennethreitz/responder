"""v9.0: opt-in CSRF protection and proxy-headers support."""

import pytest

import responder
from responder import File, UploadFile


def _api(**kwargs):
    return responder.API(
        debug=False,
        allowed_hosts=[";"],
        session_https_only=False,
        secret_key="x" * 32,
        **kwargs,
    )


def url(s):
    return f"http://;{s}"


def _echo_routes(api):
    @api.route("/token")
    async def token(req, resp):
        resp.media = {"token": req.csrf_token}

    @api.route("/submit", methods=["POST"])
    async def submit(req, resp):
        form = await req.media("form")
        resp.media = {"name": form.get("name")}


# --- CSRF -------------------------------------------------------------------


def test_csrf_off_by_default():
    api = _api()
    _echo_routes(api)
    r = api.requests.post(url("/submit"), data={"name": "k"})
    assert r.status_code == 200


def test_csrf_blocks_unsafe_methods_without_token():
    api = _api(csrf=True)
    _echo_routes(api)
    client = api.requests
    assert client.get(url("/token")).status_code == 200  # safe method
    assert client.post(url("/submit"), data={"name": "k"}).status_code == 403
    assert client.put(url("/submit")).status_code in (403, 405)


def test_csrf_header_token_allows():
    api = _api(csrf=True)
    _echo_routes(api)
    client = api.requests
    token = client.get(url("/token")).json()["token"]
    r = client.post(
        url("/submit"), data={"name": "k"}, headers={"X-CSRF-Token": token}
    )
    assert r.status_code == 200
    assert r.json() == {"name": "k"}


def test_csrf_form_field_token_allows():
    api = _api(csrf=True)
    _echo_routes(api)
    client = api.requests
    token = client.get(url("/token")).json()["token"]
    r = client.post(url("/submit"), data={"name": "k", "csrf_token": token})
    assert r.status_code == 200
    assert r.json() == {"name": "k"}


def test_csrf_wrong_token_is_403():
    api = _api(csrf=True)
    _echo_routes(api)
    client = api.requests
    client.get(url("/token"))  # establish a session token
    r = client.post(url("/submit"), data={"csrf_token": "wrong"})
    assert r.status_code == 403


def test_csrf_multipart_shares_streaming_parse():
    """The CSRF check parses the multipart form once; the handler's File
    marker and media() reads reuse that cached (spooled) parse."""
    api = _api(csrf=True)

    @api.route("/token")
    async def token(req, resp):
        resp.media = {"token": req.csrf_token}

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp, *, f: UploadFile = File(...)):
        resp.media = {"filename": f.filename, "size": len(await f.read())}

    client = api.requests
    token_value = client.get(url("/token")).json()["token"]
    r = client.post(
        url("/upload"),
        data={"csrf_token": token_value},
        files={"f": ("a.bin", b"payload")},
    )
    assert r.status_code == 200
    assert r.json() == {"filename": "a.bin", "size": 7}


def test_csrf_route_opt_out():
    api = _api(csrf=True)

    @api.route("/webhook", methods=["POST"], csrf=False)
    async def webhook(req, resp):
        resp.media = {"ok": True}

    r = api.requests.post(url("/webhook"), json={"event": "ping"})
    assert r.status_code == 200


def test_csrf_route_opt_in_with_global_off():
    api = _api()  # csrf globally off

    @api.route("/guarded", methods=["POST"], csrf=True)
    async def guarded(req, resp):
        resp.media = {"ok": True}

    r = api.requests.post(url("/guarded"), json={})
    assert r.status_code == 403


def test_csrf_requires_sessions():
    with pytest.raises(ValueError, match="csrf=True requires sessions"):
        responder.API(csrf=True, sessions=False)


def test_route_level_csrf_requires_sessions():
    """A per-route csrf=True on a sessions-less app must fail at registration,
    not 500 at request time when enforce_csrf reaches for req.session."""
    api = responder.API(allowed_hosts=[";"], sessions=False)

    with pytest.raises(ValueError, match="requires sessions"):

        @api.route("/guarded", methods=["POST"], csrf=True)
        async def guarded(req, resp):
            resp.media = {"ok": True}

    # csrf=False (an exemption) is meaningless without sessions but harmless.
    @api.route("/open", methods=["POST"], csrf=False)
    async def open_route(req, resp):
        resp.media = {"ok": True}

    assert api.requests.post(url("/open"), json={}).status_code == 200


def test_csrf_input_renders_hidden_field():
    api = _api(csrf=True)

    @api.route("/form")
    async def form(req, resp):
        resp.media = {"input": str(req.csrf_input), "token": req.csrf_token}

    body = api.requests.get(url("/form")).json()
    assert body["input"] == (
        f'<input type="hidden" name="csrf_token" value="{body["token"]}">'
    )


def test_csrf_token_is_stable_within_session():
    api = _api(csrf=True)
    _echo_routes(api)
    client = api.requests
    first = client.get(url("/token")).json()["token"]
    second = client.get(url("/token")).json()["token"]
    assert first == second


# --- Proxy headers ----------------------------------------------------------


def _proxy_api(**kwargs):
    api = responder.API(
        debug=False,
        allowed_hosts=["example.com", ";"],
        session_https_only=False,
        secret_key="x" * 32,
        **kwargs,
    )

    @api.route("/where")
    async def where(req, resp):
        resp.media = {
            "scheme": req.url.scheme,
            "host": req.headers["Host"],
            "client": req.client[0] if req.client else None,
        }

    return api


def test_proxy_headers_ignored_by_default():
    api = _proxy_api()
    r = api.requests.get(
        url("/where"),
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "203.0.113.7",
        },
    )
    body = r.json()
    assert body["scheme"] == "http"
    assert body["client"] != "203.0.113.7"


def test_x_forwarded_headers_rewrite_scope():
    api = _proxy_api(trust_proxy_headers=True)
    r = api.requests.get(
        url("/where"),
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "example.com",
            "X-Forwarded-For": "203.0.113.7, 10.0.0.1",
        },
    )
    assert r.status_code == 200  # TrustedHost validated the *forwarded* host
    assert r.json() == {
        "scheme": "https",
        "host": "example.com",
        "client": "203.0.113.7",
    }


def test_rfc7239_forwarded_header():
    api = _proxy_api(trust_proxy_headers=True)
    r = api.requests.get(
        url("/where"),
        headers={
            "Forwarded": 'for="[2001:db8::17]:4711";proto=https;host=example.com'
        },
    )
    assert r.json() == {
        "scheme": "https",
        "host": "example.com",
        "client": "2001:db8::17",
    }


def test_forwarded_takes_precedence_over_x_forwarded():
    api = _proxy_api(trust_proxy_headers=True)
    r = api.requests.get(
        url("/where"),
        headers={
            "Forwarded": "for=203.0.113.7;proto=https",
            "X-Forwarded-For": "198.51.100.9",
            "X-Forwarded-Proto": "http",
        },
    )
    body = r.json()
    assert body["scheme"] == "https"
    assert body["client"] == "203.0.113.7"


def test_x_real_ip_fallback():
    api = _proxy_api(trust_proxy_headers=True)
    r = api.requests.get(url("/where"), headers={"X-Real-IP": "198.51.100.9"})
    assert r.json()["client"] == "198.51.100.9"


def test_resolve_client_ip_matches_middleware_precedence():
    """The shared resolver (logging, rate limiting) must agree with
    ProxyHeadersMiddleware: Forwarded wins over X-Forwarded-For."""
    from responder.util.net import resolve_client_ip

    headers = {
        "forwarded": "for=203.0.113.7;proto=https",
        "x-forwarded-for": "198.51.100.9",
    }
    peer = ("10.0.0.1", 1234)

    def get(name):
        return headers.get(name.lower())

    assert resolve_client_ip(peer, get, trust_proxy_headers=True) == "203.0.113.7"
    # An unusable for= node falls back to X-Forwarded-For, like the middleware.
    headers["forwarded"] = "for=unknown;proto=https"
    assert resolve_client_ip(peer, get, trust_proxy_headers=True) == "198.51.100.9"
    # Untrusted: always the TCP peer.
    assert resolve_client_ip(peer, get, trust_proxy_headers=False) == "10.0.0.1"


def test_ratelimiter_buckets_by_forwarded_ip():
    """Same Forwarded client + varying X-Forwarded-For = one bucket."""
    from responder.ext.ratelimit import RateLimiter

    api = _api()
    limiter = RateLimiter(requests=1, period=60, trust_proxy_headers=True)

    @api.route("/limited")
    @limiter.limit
    async def limited(req, resp):
        resp.media = {"ok": True}

    def hit(xff):
        return api.requests.get(
            url("/limited"),
            headers={"Forwarded": "for=203.0.113.7", "X-Forwarded-For": xff},
        )

    assert hit("198.51.100.1").status_code == 200
    # A different XFF must not open a fresh bucket while Forwarded pins
    # the same client.
    assert hit("198.51.100.2").status_code == 429


@pytest.mark.parametrize(
    ("forwarded_host", "expected_server"),
    [
        ("example.com:8443", ["example.com", 8443]),
        ("[2001:db8::1]:8443", ["2001:db8::1", 8443]),
        ("[2001:db8::1]", ["2001:db8::1", 443]),
        ("example.com", ["example.com", 443]),
    ],
)
def test_forwarded_host_populates_scope_server(forwarded_host, expected_server):
    api = responder.API(
        debug=False, allowed_hosts=["*"], session_https_only=False,
        secret_key="x" * 32, trust_proxy_headers=True,
    )

    @api.route("/server")
    async def server(req, resp):
        resp.media = {"server": list(req._starlette.scope["server"])}

    r = api.requests.get(
        url("/server"),
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": forwarded_host,
        },
    )
    assert r.json() == {"server": expected_server}
