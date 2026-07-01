"""Response.redirect() defaults to 307, with permanent=True selecting 308."""


def test_redirect_defaults_to_temporary_307(api):
    @api.route("/old")
    def old(req, resp):
        resp.redirect("/new")

    r = api.requests.get("/old", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["Location"] == "/new"


def test_redirect_permanent_selects_308(api):
    @api.route("/old")
    def old(req, resp):
        resp.redirect("/new", permanent=True)

    r = api.requests.get("/old", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["Location"] == "/new"


def test_explicit_status_code_still_wins(api):
    @api.route("/moved")
    def moved(req, resp):
        resp.redirect("/new", status_code=301)

    @api.route("/found")
    def found(req, resp):
        resp.redirect("/new", status_code=302, permanent=True)

    assert api.requests.get("/moved", follow_redirects=False).status_code == 301
    # An explicit status_code takes precedence over permanent=.
    assert api.requests.get("/found", follow_redirects=False).status_code == 302


def test_api_redirect_helper_matches_new_default(api):
    @api.route("/a")
    def a(req, resp):
        api.redirect(resp, location="/b")

    @api.route("/p")
    def p(req, resp):
        api.redirect(resp, location="/b", permanent=True)

    assert api.requests.get("/a", follow_redirects=False).status_code == 307
    assert api.requests.get("/p", follow_redirects=False).status_code == 308


def test_redirect_still_follows(api, session):
    @api.route("/2")
    def two(req, resp):
        resp.redirect("/1")

    @api.route("/1")
    def one(req, resp):
        resp.text = "redirected"

    r = session.get("/2")
    assert r.text == "redirected"


def test_redirect_sets_body_text(api):
    @api.route("/old")
    def old(req, resp):
        resp.redirect("/new")

    r = api.requests.get("/old", follow_redirects=False)
    assert r.text == "Redirecting to: /new"
