import concurrent
import io

import pytest
import requests
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


def test_class_based_view_parameters(api):
    @api.route("/{greeting}")
    class Greeting:
        def on_request(self, req, resp, *, greeting):
            resp.text = f"{greeting}, world!"

    assert api.session().get("http://;/Hello").ok


def test_requests_session(api):
    assert api.session()
    assert api.requests


def test_requests_session_works(api, url):
    TEXT = "spiral out"

    @api.route("/")
    def hello(req, resp):
        resp.text = TEXT

    assert api.requests.get(url("/")).text == TEXT


def test_status_code(api):
    @api.route("/")
    def hello(req, resp):
        resp.text = "keep going"
        resp.status_code = responder.status_codes.HTTP_416

    assert api.requests.get("http://;/").status_code == responder.status_codes.HTTP_416


def test_json_media(api):
    dump = {"life": 42}

    @api.route("/")
    def media(req, resp):
        resp.media = dump

    r = api.requests.get("http://;/")

    assert "json" in r.headers["Content-Type"]
    assert r.json() == dump


def test_yaml_media(api):
    dump = {"life": 42}

    @api.route("/")
    def media(req, resp):
        resp.media = dump

    r = api.requests.get("http://;/", headers={"Accept": "yaml"})

    assert "yaml" in r.headers["Content-Type"]
    assert yaml.load(r.content) == dump


def test_graphql_schema_query_querying(api, schema):
    api.add_route("/", responder.ext.GraphQLView(schema=schema, api=api))

    r = api.requests.get("http://;/?q={ hello }", headers={"Accept": "json"})
    assert r.json() == {"data": {"hello": "Hello stranger"}}


def test_argumented_routing(api):
    @api.route("/{name}")
    def hello(req, resp, *, name):
        resp.text = f"Hello, {name}."

    r = api.requests.get(api.url_for(hello, name="sean"))
    assert r.text == "Hello, sean."


def test_mote_argumented_routing(api):
    @api.route("/{greeting}/{name}")
    def hello(req, resp, *, greeting, name):
        resp.text = f"{greeting}, {name}."

    r = api.requests.get(api.url_for(hello, greeting="hello", name="lyndsy"))
    assert r.text == "hello, lyndsy."


def test_request_and_get(api):
    @api.route("/")
    class ThingsResource:
        def on_request(self, req, resp):
            resp.headers.update({"DEATH": "666"})

        def on_get(self, req, resp):
            resp.headers.update({"LIFE": "42"})

    r = api.requests.get(api.url_for(ThingsResource))
    assert "DEATH" in r.headers
    assert "LIFE" in r.headers


def test_class_based_view_status_code(api):
    @api.route("/")
    class ThingsResource:
        def on_request(self, req, resp):
            resp.status_code = responder.status_codes.HTTP_416

    assert api.requests.get("http://;/").status_code == responder.status_codes.HTTP_416


def test_query_params(api, url):
    @api.route("/")
    def route(req, resp):
        resp.media = {"params": req.params}

    r = api.requests.get(api.url_for(route), params={"q": "q"})
    assert r.json()["params"] == {"q": "q"}

    r = api.requests.get(url("/?q=1&q=2&q=3"))
    assert r.json()["params"] == {"q": "3"}


# Requires https://github.com/encode/starlette/pull/102
def test_form_data(api):
    @api.route("/")
    async def route(req, resp):
        resp.media = {"form": await req.media("form")}

    dump = {"q": "q"}
    r = api.requests.get(api.url_for(route), data=dump)
    assert r.json()["form"] == dump


def test_async_function(api):
    content = "The Emerald Tablet of Hermes"

    @api.route("/")
    async def route(req, resp):
        resp.text = content

    r = api.requests.get(api.url_for(route))
    assert r.text == content


def test_media_parsing(api):
    dump = {"hello": "sam"}

    @api.route("/")
    def route(req, resp):
        resp.media = dump

    r = api.requests.get(api.url_for(route))
    assert r.json() == dump

    r = api.requests.get(api.url_for(route), headers={"Accept": "application/x-yaml"})
    assert r.text == "{hello: sam}\n"


def test_background(api):
    @api.route("/")
    def route(req, resp):
        @api.background.task
        def task():
            import time

            time.sleep(3)

        task()
        api.text = "ok"

    r = api.requests.get(api.url_for(route))
    assert r.ok


def test_multiple_routes(api):
    @api.route("/1")
    def route1(req, resp):
        resp.text = "1"

    @api.route("/2")
    def route2(req, resp):
        resp.text = "2"

    r = api.requests.get(api.url_for(route1))
    assert r.text == "1"

    r = api.requests.get(api.url_for(route2))
    assert r.text == "2"


def test_graphql_schema_json_query(api, schema):
    api.add_route("/", responder.ext.GraphQLView(schema=schema, api=api))

    r = api.requests.post("http://;/", json={"query": "{ hello }"})
    assert r.ok


def test_graphiql(api, schema):
    api.add_route("/", responder.ext.GraphQLView(schema=schema, api=api))

    r = api.requests.get("http://;/", headers={"Accept": "text/html"})
    assert r.ok
    assert "GraphiQL" in r.text


def test_json_uploads(api):
    @api.route("/")
    async def route(req, resp):
        resp.media = await req.media()

    dump = {"complicated": "times"}
    r = api.requests.post(api.url_for(route), json=dump)
    assert r.json() == dump


def test_yaml_uploads(api):
    @api.route("/")
    async def route(req, resp):
        resp.media = await req.media()

    dump = {"complicated": "times"}
    r = api.requests.post(
        api.url_for(route),
        data=yaml.dump(dump),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert r.json() == dump


def test_form_uploads(api):
    @api.route("/")
    async def route(req, resp):
        resp.media = await req.media()

    dump = {"complicated": "times"}
    r = api.requests.post(api.url_for(route), data=dump)
    assert r.json() == dump


def test_json_downloads(api):
    dump = {"testing": "123"}

    @api.route("/")
    def route(req, resp):
        resp.media = dump

    r = api.requests.get(
        api.url_for(route), headers={"Content-Type": "application/json"}
    )
    assert r.json() == dump


def test_yaml_downloads(api):
    dump = {"testing": "123"}

    @api.route("/")
    def route(req, resp):
        resp.media = dump

    r = api.requests.get(
        api.url_for(route), headers={"Content-Type": "application/x-yaml"}
    )
    assert yaml.safe_load(r.content) == dump


def test_schema_generation():
    import responder
    from marshmallow import Schema, fields

    api = responder.API(
        title="Web Service", openapi="3.0", allowed_hosts=["testserver", ";"]
    )

    @api.schema("Pet")
    class PetSchema(Schema):
        name = fields.Str()

    @api.route("/")
    def route(req, resp):
        """A cute furry animal endpoint.
        ---
        get:
            description: Get a random pet
            responses:
                200:
                    description: A pet to be returned
                    schema:
                        $ref = "#/components/schemas/Pet"
        """
        resp.media = PetSchema().dump({"name": "little orange"})

    r = api.requests.get("http://;/schema.yml")
    dump = yaml.safe_load(r.content)

    assert dump
    assert dump["openapi"] == "3.0"


def test_documentation():
    import responder
    from marshmallow import Schema, fields

    api = responder.API(
        title="Web Service",
        openapi="3.0",
        docs_route="/docs",
        allowed_hosts=["testserver", ";"],
    )

    @api.schema("Pet")
    class PetSchema(Schema):
        name = fields.Str()

    @api.route("/")
    def route(req, resp):
        """A cute furry animal endpoint.
        ---
        get:
            description: Get a random pet
            responses:
                200:
                    description: A pet to be returned
                    schema:
                        $ref = "#/components/schemas/Pet"
        """
        resp.media = PetSchema().dump({"name": "little orange"})

    r = api.requests.get("/docs")
    assert "html" in r.text


def test_mount_wsgi_app(api, flask):
    @api.route("/")
    def hello(req, resp):
        resp.text = "hello"

    api.mount("/flask", flask)

    r = api.requests.get("http://;/flask")
    assert r.ok


def test_async_class_based_views(api):
    @api.route("/")
    class Resource:
        async def on_post(self, req, resp):
            resp.text = await req.text

    data = "frame"
    r = api.requests.post(api.url_for(Resource), data=data)
    assert r.text == data


def test_cookies(api):
    @api.route("/")
    def cookies(req, resp):
        resp.media = {"cookies": req.cookies}
        resp.cookies["foo"] = "bar"
        resp.cookies["sent"] = "true"

    r = api.requests.get(api.url_for(cookies), cookies={"hello": "universe"})
    assert r.json() == {"cookies": {"hello": "universe"}}
    assert "sent" in r.cookies
    assert r.cookies["foo"] == "bar"

    r = api.requests.get(api.url_for(cookies))
    cookies = r.json()["cookies"]
    assert cookies["sent"] == "true"
    assert cookies["foo"] == "bar"


@pytest.mark.xfail
def test_sessions(api):
    @api.route("/")
    def view(req, resp):
        resp.session["hello"] = "world"
        resp.media = resp.session

    r = api.requests.get(api.url_for(view))
    assert "Responder-Session" in r.cookies

    r = api.requests.get(api.url_for(view))
    assert (
        r.cookies["Responder-Session"]
        == '{"hello": "world"}.r3EB04hEEyLYIJaAXCEq3d4YEbs'
    )
    assert r.json() == {"hello": "world"}


def test_template_rendering(api):
    @api.route("/")
    def view(req, resp):
        resp.content = api.template_string("{{ var }}", var="hello")

    r = api.requests.get(api.url_for(view))
    assert r.text == "hello"


def test_file_uploads(api):
    @api.route("/")
    async def upload(req, resp):

        files = await req.media("files")
        result = {}
        result["hello"] = files["hello"]["content"].decode("utf-8")
        result["not-a-file"] = files["not-a-file"].decode("utf-8")
        resp.media = {"files": result}

    world = io.StringIO("world")
    data = {"hello": ("hello.txt", world, "text/plain"), "not-a-file": b"data only"}
    r = api.requests.post(api.url_for(upload), files=data)
    assert r.json() == {"files": {"hello": "world", "not-a-file": "data only"}}


def test_500(api):
    @api.route("/")
    def view(req, resp):
        raise ValueError

    dumb_client = responder.api.TestClient(
        api, base_url="http://;", raise_server_exceptions=False
    )
    r = dumb_client.get(api.url_for(view))
    assert not r.ok
    assert r.status_code == responder.status_codes.HTTP_500


def test_404(api):
    r = api.requests.get("/foo")

    assert r.status_code == responder.status_codes.HTTP_404


def test_kinda_websockets(api):
    @api.route("/ws", websocket=True)
    async def websocket(ws):
        await ws.accept()
        await ws.send_text("Hello via websocket!")
        await ws.close()


@pytest.mark.xfail
def test_startup(api, session):
    who = [None]

    @api.route("/{greeting}")
    async def greet_world(req, resp, *, greeting):
        resp.text = f"{greeting}, {who[0]}!"

    @api.on_event("startup")
    async def asd():
        who[0] = "world"
        print("startup")

    @api.on_event("cleanup")
    async def asd():
        print("cleanup")

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    f = pool.submit(api.run)

    r = requests.get(f"http://localhost:5042/hello")
    assert r.text == "hello, world!"


def test_redirects(api, session):
    @api.route("/2")
    def two(req, resp):
        api.redirect(resp, location="/1")

    @api.route("/1")
    def one(req, resp):
        resp.text = "redirected"

    assert session.get("/1").url == "http://;/1"


def test_session_thoroughly(api, session):
    @api.route("/set")
    def set(req, resp):
        resp.session["hello"] = "world"
        api.redirect(resp, location="/get")

    @api.route("/get")
    def get(req, resp):
        resp.media = {"session": req.session}

    r = session.get(api.url_for(set))
    r = session.get(api.url_for(get))
    assert r.json() == {"session": {"hello": "world"}}


def test_before_response(api, session):
    @api.route("/get")
    def get(req, resp):
        resp.media = req.session

    @api.route(before_request=True)
    def before_request(req, resp):
        resp.headers["x-pizza"] = "1"

    r = session.get(api.url_for(get))
    assert "x-pizza" in r.headers


def test_allowed_hosts():
    api = responder.API(allowed_hosts=[";", "tenant.;"])

    @api.route("/")
    def get(req, resp):
        pass

    # Exact match
    r = api.requests.get(api.url_for(get))
    assert r.status_code == 200

    # Reset the session
    api._session = None
    r = api.session(base_url="http://tenant.;").get(api.url_for(get))
    assert r.status_code == 200

    # Reset the session
    api._session = None
    r = api.session(base_url="http://unkownhost").get(api.url_for(get))
    assert r.status_code == 400

    # Reset the session
    api._session = None
    r = api.session(base_url="http://unkown_tenant.;").get(api.url_for(get))
    assert r.status_code == 400

    api = responder.API(allowed_hosts=["*.;"])

    @api.route("/")
    def get(req, resp):
        pass

    # Wildcard domains
    # Using http://;
    r = api.requests.get(api.url_for(get))
    assert r.status_code == 400

    # Reset the session
    api._session = None
    r = api.session(base_url="http://tenant1.;").get(api.url_for(get))
    assert r.status_code == 200

    # Reset the session
    api._session = None
    r = api.session(base_url="http://tenant2.;").get(api.url_for(get))
    assert r.status_code == 200
