"""v5.3: route metadata kwargs (tags/summary/...) and servers."""

import yaml
from starlette.testclient import TestClient

import responder


def _client(api):
    return TestClient(api, base_url="http://;")


def test_route_metadata_emitted():
    api = responder.API(
        title="T", version="1", openapi="3.0.2",
        allowed_hosts=[";"], session_https_only=False,
    )

    @api.get("/users", tags=["users"], summary="List users", operation_id="listUsers")
    def list_users(req, resp):
        resp.media = []

    @api.post("/users", tags=["users"], deprecated=True, description="Create a user")
    def create_user(req, resp):
        resp.media = {}

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    get = spec["paths"]["/users"]["get"]
    post = spec["paths"]["/users"]["post"]
    assert get["tags"] == ["users"]
    assert get["summary"] == "List users"
    assert get["operationId"] == "listUsers"
    assert post["deprecated"] is True
    assert post["description"] == "Create a user"


def test_docstring_yaml_overrides_route_metadata():
    api = responder.API(
        title="T", version="1", openapi="3.0.2",
        allowed_hosts=[";"], session_https_only=False,
    )

    @api.get("/x", tags=["auto"], summary="Route summary")
    def x(req, resp):
        """Doc.
        ---
        get:
            summary: Docstring wins
        """
        resp.text = "x"

    op = yaml.safe_load(_client(api).get("/schema.yml").content)["paths"]["/x"]["get"]
    assert op["summary"] == "Docstring wins"  # docstring override
    assert op["tags"] == ["auto"]  # untouched route metadata preserved


def test_marker_params_are_operation_level_not_shared_across_methods():
    # Regression: query/marker params must live on the operation, not the path
    # item — otherwise a sibling method on the same path inherits them.
    import responder
    from responder import Query

    api = responder.API(
        title="T", version="1", openapi="3.1.0",
        allowed_hosts=[";"], session_https_only=False,
    )

    @api.get("/items")
    def list_items(req, resp, *, q: str = Query(...), limit: int = Query(10)):
        resp.media = []

    @api.post("/items")
    def create_item(req, resp):  # no query params
        resp.media = {}

    item = yaml.safe_load(_client(api).get("/schema.yml").content)["paths"]["/items"]
    assert "parameters" not in item  # nothing leaks to the path level
    assert {p["name"] for p in item["get"]["parameters"]} == {"q", "limit"}
    assert "parameters" not in item["post"]  # POST has none of its own


def test_servers_emitted():
    api = responder.API(
        title="T", version="1", openapi="3.0.2",
        allowed_hosts=[";"], session_https_only=False,
        openapi_servers=[{"url": "https://api.example.com", "description": "prod"}],
    )

    @api.route("/ping")
    def ping(req, resp):
        resp.text = "pong"

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    assert spec["servers"] == [
        {"url": "https://api.example.com", "description": "prod"}
    ]
