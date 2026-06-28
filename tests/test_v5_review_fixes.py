"""Regression tests for bugs caught by the adversarial review of the v5 diff."""

import yaml

import responder
from responder import Path, Query

KEY = "a-real-private-secret-key-32chars!"


def _api(**kw):
    kw.setdefault("allowed_hosts", [";"])
    kw.setdefault("session_https_only", False)
    return responder.API(**kw)


# 1 (high): resp.session = {...} must not 500 on the default cookie backend.
def test_resp_session_whole_assignment_persists():
    api = _api(secret_key=KEY)

    @api.route("/login", methods=["POST"])
    def login(req, resp):
        resp.session = {"user": "kenneth"}
        resp.text = "ok"

    @api.route("/me")
    def me(req, resp):
        resp.media = {"user": req.session.get("user")}

    client = api.requests
    assert client.post("/login").status_code == 200
    assert client.get("/me").json() == {"user": "kenneth"}


# 3 (medium): class-based view markers must resolve, not leak the sentinel.
def test_class_based_view_markers_resolve():
    api = _api()

    @api.route("/items")
    class Items:
        def on_get(self, req, resp, *, q: str = Query("none")):
            resp.media = {"q": q}

    assert api.requests.get("/items?q=hello").json() == {"q": "hello"}
    assert api.requests.get("/items").json() == {"q": "none"}


# 6 (low): Path marker alias is honored and the raw key doesn't leak.
def test_path_marker_alias():
    api = _api()

    @api.route("/u/{uid}")
    def u(req, resp, *, user_id: int = Path(..., alias="uid")):
        resp.media = {"user_id": user_id}

    assert api.requests.get("/u/42").json() == {"user_id": 42}


# 7 (low): a bare `list` annotation reads repeated query keys.
def test_bare_list_query_is_multivalue():
    api = _api()

    @api.route("/items")
    def items(req, resp, *, ids: list = Query(...)):
        resp.media = {"ids": ids}

    assert api.requests.get("/items?ids=1&ids=2").json() == {"ids": ["1", "2"]}


# 2/4 (medium): a dependency-injected Pydantic param is not a phantom body.
def test_openapi_no_phantom_body_for_dependency(needs_openapi):
    from pydantic import BaseModel

    class User(BaseModel):
        name: str

    api = _api(openapi="3.0.2")

    @api.dependency()
    def current_user():
        return User(name="k")

    @api.route("/me")
    def me(req, resp, *, current_user: User):
        resp.media = {"name": current_user.name}

    spec = yaml.safe_load(api.requests.get("http://;/schema.yml").content)
    op = spec["paths"]["/me"]["get"]
    assert "requestBody" not in op
    assert "422" not in op.get("responses", {})


# 5 (medium): a request_model route without methods= is documented as POST.
def test_openapi_request_model_route_documents_post(needs_openapi):
    from pydantic import BaseModel

    class ItemIn(BaseModel):
        name: str

    api = _api(openapi="3.0.2")

    @api.route("/items", request_model=ItemIn)
    async def create(req, resp):
        resp.media = {"ok": True}

    item = yaml.safe_load(api.requests.get("http://;/schema.yml").content)["paths"][
        "/items"
    ]
    assert "post" in item
    assert "requestBody" in item["post"]
    assert "get" not in item
