"""Request.cookies uses tolerant cookie parsing (not SimpleCookie)."""


def test_nonconforming_cookie_token_does_not_drop_later_cookies(api):
    # http.cookies.SimpleCookie aborts at the first nonconforming token
    # (here "b[]"), silently dropping every cookie after it.
    @api.route("/")
    def view(req, resp):
        resp.media = req.cookies

    r = api.requests.get("/", headers={"Cookie": "a=1; b[]=2; c=3"})
    cookies = r.json()
    assert cookies["a"] == "1"
    assert cookies["c"] == "3"


def test_plain_cookies_still_parse(api):
    @api.route("/")
    def view(req, resp):
        resp.media = req.cookies

    r = api.requests.get("/", headers={"Cookie": "hello=world; foo=bar"})
    assert r.json() == {"hello": "world", "foo": "bar"}


def test_no_cookie_header_yields_empty_dict(api):
    @api.route("/")
    def view(req, resp):
        resp.media = {"cookies": req.cookies}

    r = api.requests.get("/")
    assert r.json() == {"cookies": {}}


def test_cookies_are_cached_per_request(api):
    @api.route("/")
    def view(req, resp):
        first = req.cookies
        first["injected"] = "yes"
        # The property caches, so the same dict comes back.
        resp.media = {"same": req.cookies is first}

    r = api.requests.get("/", headers={"Cookie": "a=1"})
    assert r.json() == {"same": True}
