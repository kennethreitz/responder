"""URL building, flash messages, Link headers, and partitioned cookies."""

import pytest

import responder
from responder.routes import RouteNotFoundError


def test_request_url_for_path_and_external(api):
    @api.route("/users/{user_id}", name="user")
    def user(req, resp, *, user_id):
        resp.media = {
            "path": req.url_for("user", user_id=user_id),
            "by_func": req.url_for(user, user_id=user_id),
            "absolute": req.url_for("user", user_id=user_id, _external=True),
            "base": req.base_url,
        }

    response = api.requests.get("/users/ada")
    body = response.json()
    assert body["path"] == "/users/ada"
    assert body["by_func"] == "/users/ada"
    assert body["absolute"] == body["base"] + "/users/ada"
    assert body["absolute"].startswith("http://")
    assert "/users/ada" in body["absolute"]


def test_request_url_for_keeps_param_named_external(api):
    @api.route("/go/{external}")
    def go(req, resp, *, external):
        resp.media = {
            "path": req.url_for(go, external=external),
            "absolute": req.url_for(go, external=external, _external=True),
        }

    body = api.requests.get("/go/elsewhere").json()
    assert body["path"] == "/go/elsewhere"
    assert body["absolute"].endswith("/go/elsewhere")


def test_request_url_for_unknown_route_raises(api):
    @api.route("/missing")
    def missing(req, resp):
        req.url_for("no-such-route")

    with pytest.raises(RouteNotFoundError):
        api.requests.get("/missing")


def test_flash_round_trip_and_categories(api):
    @api.route("/set")
    def set_flash(req, resp):
        req.flash("Saved.")
        req.flash("Name is required.", category="error")
        resp.text = "ok"

    @api.route("/errors")
    def errors(req, resp):
        resp.media = {
            "errors": req.flashes(category="error"),
            "rest": req.flashes(with_categories=True),
        }

    client = api.requests
    assert client.get("/set").status_code == 200
    body = client.get("/errors").json()
    assert body["errors"] == ["Name is required."]
    assert body["rest"] == [["message", "Saved."]]
    # The queue is empty once both calls have drained it.
    again = client.get("/errors").json()
    assert again == {"errors": [], "rest": []}


def test_flash_drops_oldest_past_the_cap(api):
    @api.route("/many")
    def many(req, resp):
        for index in range(33):
            req.flash(f"m{index}")
        resp.media = {"notes": req.flashes()}

    notes = api.requests.get("/many").json()["notes"]
    assert notes[0] == "m1"
    assert notes[-1] == "m32"
    assert len(notes) == 32


def test_flash_rejects_non_strings_and_corrupt_session_data(api):
    @api.route("/bad")
    def bad(req, resp):
        req.flash({"nope": True})

    with pytest.raises(TypeError):
        api.requests.get("/bad")

    @api.route("/junk")
    def junk(req, resp):
        req.session["_responder_flashes"] = "not-a-list"
        resp.media = {"notes": req.flashes()}

    assert api.requests.get("/junk").json() == {"notes": []}


def test_flash_requires_sessions():
    api = responder.API(sessions=False, allowed_hosts=[";"])

    @api.route("/")
    def view(req, resp):
        req.flash("hi")

    with pytest.raises(RuntimeError, match="sessions"):
        api.requests.get("/")


def test_render_passes_get_flashed_messages(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "hi.html").write_text(
        "{% for note in get_flashed_messages() %}<p>{{ note }}</p>{% endfor %}"
    )
    api = responder.API(
        templates_dir=str(templates),
        allowed_hosts=[";"],
        session_https_only=False,
    )

    @api.route("/set")
    def set_flash(req, resp):
        req.flash("Saved.")
        resp.text = "ok"

    @api.route("/page")
    def page(req, resp):
        resp.render("hi.html")

    client = api.requests
    assert client.get("/set").status_code == 200
    rendered = client.get("/page")
    assert rendered.text == "<p>Saved.</p>"
    assert "text/html" in rendered.headers["content-type"]
    # Consumed by the template, so a second render is empty.
    assert client.get("/page").text == ""


def test_render_keeps_caller_supplied_flash_helper(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "hi.html").write_text("{{ get_flashed_messages() }}")
    api = responder.API(
        templates_dir=str(templates),
        allowed_hosts=[";"],
        session_https_only=False,
    )

    @api.route("/")
    def page(req, resp):
        req.flash("from-session")
        resp.render("hi.html", get_flashed_messages=lambda **kwargs: "override")

    @api.route("/left")
    def left(req, resp):
        resp.media = {"notes": req.flashes()}

    client = api.requests
    assert client.get("/").text == "override"
    assert client.get("/left").json() == {"notes": ["from-session"]}


def test_link_appends_and_scrubs(api):
    @api.route("/links")
    def links(req, resp):
        resp.link("/items?page=2", rel="next")
        resp.link("/schema", rel="describedby", type="application/json")
        resp.link("/next\r\nX-Injected: pwned>", rel='next"\ninjected')
        resp.text = "ok"

    response = api.requests.get("/links")
    header = response.headers["link"]
    assert header.startswith('</items?page=2>; rel="next"')
    assert 'rel="describedby"; type="application/json"' in header
    assert "\r" not in header and "\n" not in header
    assert response.headers.get("x-injected") is None
    assert "%3E" in header


def test_link_rejects_empty_target(api):
    @api.route("/links")
    def links(req, resp):
        resp.link("", rel="next")

    with pytest.raises(ValueError, match="url"):
        api.requests.get("/links")


def test_partitioned_cookie_round_trip(api):
    @api.route("/set")
    def set_cookie(req, resp):
        resp.set_cookie("sid", "abc", secure=True, partitioned=True)
        resp.text = "ok"

    @api.route("/clear")
    def clear(req, resp):
        resp.delete_cookie("sid", secure=True, partitioned=True)
        resp.text = "cleared"

    set_response = api.requests.get("/set")
    cookie = set_response.headers["set-cookie"]
    assert "sid=abc" in cookie
    assert "Partitioned" in cookie
    assert "Secure" in cookie

    cleared = api.requests.get("/clear")
    deletion = cleared.headers["set-cookie"]
    assert "Partitioned" in deletion
    assert "Max-Age=0" in deletion


def test_partitioned_cookie_requires_secure(api):
    @api.route("/set")
    def set_cookie(req, resp):
        resp.set_cookie("sid", "abc", partitioned=True)

    with pytest.raises(ValueError, match="Secure"):
        api.requests.get("/set")
