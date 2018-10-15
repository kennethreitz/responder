import pytest
import yaml
import responder


def test_api_basic_route(api):
    @api.route("/")
    def home(req, resp):
        resp.text = "hello world!"


def test_api_basic_route_overlap(api):
    @api.route("/")
    def home(req, resp):
        resp.text = "hello world!"

    with pytest.raises(AssertionError):

        @api.route("/")
        def home2(req, resp):
            resp.text = "hello world!"


def test_api_basic_route_overlap_alternative(api):
    @api.route("/")
    def home(req, resp):
        resp.text = "hello world!"

    def home2(req, resp):
        resp.text = "hello world!"

    with pytest.raises(AssertionError):
        api.add_route("/", home2)


def test_api_basic_route_overlap_allowed(api):
    @api.route("/")
    def home(req, resp):
        resp.text = "hello world!"

    def home2(req, resp):
        resp.text = "hello world!"

    api.add_route("/", home2, check_existing=False)


def test_api_basic_route_overlap_allowed_alternative(api):
    @api.route("/")
    def home(req, resp):
        resp.text = "hello world!"

    @api.route("/", check_existing=False)
    def home2(req, resp):
        resp.text = "hello world!"


def test_class_based_view_registration(api):
    @api.route("/")
    class ThingsResource:
        def on_request(req, resp):
            resp.text = "42"


def test_requests_session(api):
    assert api.session()


def test_requests_session_works(api, session, url):
    TEXT = "spiral out"

    @api.route("/")
    def hello(req, resp):
        resp.text = TEXT

    assert session.get(url("/")).text == TEXT


def test_status_code(api):
    @api.route("/")
    def hello(req, resp):
        resp.text = "keep going"
        resp.status_code = responder.status_codes.HTTP_416

    assert api.session().get("http://;/").status_code == responder.status_codes.HTTP_416


def test_json_media(api):
    dump = {"life": 42}

    @api.route("/")
    def media(req, resp):
        resp.media = dump

    r = api.session().get("http://;/")

    assert "json" in r.headers["Content-Type"]
    assert r.json() == dump


def test_yaml_media(api):
    dump = {"life": 42}

    @api.route("/")
    def media(req, resp):
        resp.media = dump

    r = api.session().get("http://;/", headers={"Accept": "yaml"})

    assert "yaml" in r.headers["Content-Type"]
    assert yaml.load(r.content) == dump


def test_graphql_schema_query_querying(api, schema):
    api.add_route("/", schema)

    r = api.session().get("http://;/?q={ hello }", headers={"Accept": "json"})
    assert r.json() == {"data": {"hello": "Hello stranger"}}


def test_argumented_routing(api, session):
    @api.route("/{name}")
    def hello(req, resp, *, name):
        resp.text = f"Hello, {name}."

    r = session.get(api.url_for(hello, name="sean"))
    assert r.text == "Hello, sean."


def test_mote_argumented_routing(api, session):
    @api.route("/{greeting}/{name}")
    def hello(req, resp, *, greeting, name):
        resp.text = f"{greeting}, {name}."

    r = session.get(api.url_for(hello, greeting="hello", name="lyndsy"))
    assert r.text == "hello, lyndsy."


def test_request_and_get(api, session):
    @api.route("/")
    class ThingsResource:
        def on_request(self, req, resp):
            resp.headers.update({"DEATH": "666"})

        def on_get(self, request, resp):
            resp.headers.update({"LIFE": "42"})

    r = session.get(api.url_for(ThingsResource))
    assert "DEATH" in r.headers
    assert "LIFE" in r.headers


def test_query_params(api, url, session):
    @api.route("/")
    def route(req, resp):
        resp.media = {"params": req.params}

    r = session.get(api.url_for(route), params={"q": "q"})
    assert r.json()["params"] == {"q": "q"}

    r = session.get(url("/?q=1&q=2&q=3"))
    assert r.json()["params"] == {"q": "3"}


# Requires https://github.com/encode/starlette/pull/102
def test_form_data(api, session):
    @api.route("/")
    async def route(req, resp):
        resp.media = {"form": await req.media("form")}

    dump = {"q": "q"}
    r = session.get(api.url_for(route), data=dump)
    assert r.json()["form"] == dump


def test_async_function(api, session):
    content = "The Emerald Tablet of Hermes"

    @api.route("/")
    async def route(req, resp):
        resp.text = content

    r = session.get(api.url_for(route))
    assert r.text == content


def test_media_parsing(api, session):
    dump = {"hello": "sam"}

    @api.route("/")
    def route(req, resp):
        resp.media = dump

    r = session.get(api.url_for(route))
    assert r.json() == dump

    r = session.get(api.url_for(route), headers={"Accept": "application/x-yaml"})
    assert r.text == "{hello: sam}\n"


def test_background(api, session):
    @api.route("/")
    def route(req, resp):
        @api.background.task
        def task():
            import time

            time.sleep(3)

        task()
        api.text = "ok"

    r = session.get(api.url_for(route))
    assert r.ok


def test_multiple_routes(api, session):
    @api.route("/1")
    def route1(req, resp):
        resp.text = "1"

    @api.route("/2")
    def route2(req, resp):
        resp.text = "2"

    r = session.get(api.url_for(route1))
    assert r.text == "1"

    r = session.get(api.url_for(route2))
    assert r.text == "2"


def test_graphql_schema_json_query(api, schema):
    api.add_route("/", schema)

    r = api.session().post("http://;/", json={"query": "{ hello }"})
    assert r.ok
