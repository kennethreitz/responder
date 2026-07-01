"""v8.0: standalone ``responder.Router`` composition + ``api.include_router``."""

import pytest
import yaml
from starlette.testclient import TestClient

import responder
from responder import Depends, Router
from responder.ext.auth import BearerAuth


def _openapi_api(**kwargs):
    return responder.API(
        title="T", version="1", openapi="3.0.2", secret_key="x" * 32,
        allowed_hosts=[";"], session_https_only=False, **kwargs,
    )


# --- declaration without an API --------------------------------------------


def test_router_declares_routes_without_an_api():
    router = Router(prefix="/users")

    @router.route("/{user_id:int}")
    def get_user(req, resp, *, user_id):
        resp.media = {"id": user_id}

    # The decorator records the declaration and returns the function unchanged.
    assert get_user.__name__ == "get_user"
    assert "routes=1" in repr(router)


def test_router_add_route_requires_endpoint_and_path():
    router = Router()

    with pytest.raises(ValueError, match="endpoint is required"):
        router.add_route("/x")

    with pytest.raises(ValueError, match="route path is required"):
        router.add_route(None, lambda req, resp: None)


def test_router_prefix_must_start_with_slash():
    with pytest.raises(ValueError, match="must start with '/'"):
        Router(prefix="users")


def test_router_group_dependencies_must_be_depends_markers():
    with pytest.raises(TypeError, match="Depends"):
        Router(dependencies=["not-a-marker"])


# --- include with prefixes --------------------------------------------------


def test_include_router_with_prefix(api):
    router = Router()

    @router.route("/ping")
    def ping(req, resp):
        resp.text = "pong"

    api.include_router(router, prefix="/v1")

    assert api.requests.get("/v1/ping").text == "pong"
    assert api.requests.get("/ping").status_code == 404
    assert api.url_for(ping) == "/v1/ping"


def test_router_own_prefix_composes_with_include_prefix(api):
    router = Router(prefix="/users")

    @router.route("/{user_id:int}")
    def get_user(req, resp, *, user_id):
        resp.media = {"id": user_id}

    api.include_router(router, prefix="/v1")

    assert api.requests.get("/v1/users/7").json() == {"id": 7}


def test_trailing_slash_prefixes_normalize(api):
    router = Router(prefix="/users/")

    @router.route("/list")
    def list_users(req, resp):
        resp.media = []

    api.include_router(router, prefix="/v1/")

    assert api.requests.get("/v1/users/list").status_code == 200


def test_nested_include_composes_prefixes(api):
    leaf = Router(prefix="/leaf")

    @leaf.route("/item")
    def item(req, resp):
        resp.media = {"leaf": True}

    mid = Router(prefix="/mid")
    mid.include_router(leaf, prefix="/deep")

    api.include_router(mid, prefix="/root")

    assert api.requests.get("/root/mid/deep/leaf/item").json() == {"leaf": True}


def test_router_verb_sugar(api):
    router = Router()

    @router.get("/resource")
    def get_resource(req, resp):
        resp.media = {"method": "get"}

    @router.post("/resource")
    def post_resource(req, resp):
        resp.media = {"method": "post"}

    api.include_router(router)

    assert api.requests.get("/resource").json() == {"method": "get"}
    assert api.requests.post("/resource").json() == {"method": "post"}
    assert api.requests.put("/resource").status_code == 405


def test_router_websocket_route(api):
    router = Router()

    @router.websocket_route("/ws")
    async def ws(ws):
        await ws.accept()
        await ws.send_text("hi")
        await ws.close()

    api.include_router(router, prefix="/rt")

    client = TestClient(api)
    with client.websocket_connect("ws://;/rt/ws") as conn:
        assert conn.receive_text() == "hi"


# --- tags merge into OpenAPI ------------------------------------------------


def test_tags_merge_into_openapi_schema():
    api = _openapi_api()
    router = Router(tags=["users"])

    @router.get("/users", tags=["list"])
    def list_users(req, resp):
        resp.media = []

    api.include_router(router, prefix="/v1", tags=["v1"])

    spec = yaml.safe_load(api.requests.get("/schema.yml").content)
    op = spec["paths"]["/v1/users"]["get"]
    assert op["tags"] == ["v1", "users", "list"]


def test_duplicate_tags_are_dropped():
    api = _openapi_api()
    router = Router(tags=["users"])

    @router.get("/users", tags=["users", "detail"])
    def list_users(req, resp):
        resp.media = []

    api.include_router(router)

    spec = yaml.safe_load(api.requests.get("/schema.yml").content)
    assert spec["paths"]["/users"]["get"]["tags"] == ["users", "detail"]


# --- group-level dependencies -----------------------------------------------


def test_group_dependencies_run_before_handler(api):
    events = []

    def guard(req):
        events.append("guard")

    router = Router(dependencies=[Depends(guard)])

    @router.route("/thing")
    def thing(req, resp):
        events.append("handler")
        resp.media = {"ok": True}

    api.include_router(router)

    assert api.requests.get("/thing").json() == {"ok": True}
    assert events == ["guard", "handler"]


def test_dependencies_concatenate_group_first(api):
    order = []

    def from_include(req):
        order.append("include")

    def from_router(req):
        order.append("router")

    def from_route(req):
        order.append("route")

    router = Router(dependencies=[Depends(from_router)])

    @router.route("/x", dependencies=[Depends(from_route)])
    def x(req, resp):
        resp.media = {}

    api.include_router(router, dependencies=[Depends(from_include)])

    api.requests.get("/x")
    assert order == ["include", "router", "route"]


# --- group-level auth ---------------------------------------------------------


def test_group_auth_enforced():
    api = _openapi_api()
    router = Router(auth=BearerAuth(tokens=["s3cret"]))

    @router.get("/private")
    def private(req, resp, *, user):
        resp.media = {"user": user}

    @router.get("/public", auth=None)
    def public(req, resp):
        resp.media = {"open": True}

    api.include_router(router, prefix="/v1")

    assert api.requests.get("/v1/private").status_code == 401
    assert api.requests.get(
        "/v1/private", headers={"Authorization": "Bearer s3cret"}
    ).json() == {"user": "s3cret"}
    # Route-level auth=None opts out of the group auth.
    assert api.requests.get("/v1/public").json() == {"open": True}


def test_include_level_auth_applies_when_router_has_none():
    api = _openapi_api()
    router = Router()

    @router.get("/private")
    def private(req, resp, *, user):
        resp.media = {"user": user}

    api.include_router(router, auth=BearerAuth(tokens=["tok"]))

    assert api.requests.get("/private").status_code == 401
    assert api.requests.get(
        "/private", headers={"Authorization": "Bearer tok"}
    ).status_code == 200


def test_route_auth_overrides_group_auth():
    api = _openapi_api()
    router = Router(auth=BearerAuth(tokens=["group"]))

    @router.get("/own", auth=BearerAuth(tokens=["route"]))
    def own(req, resp, *, user):
        resp.media = {"user": user}

    api.include_router(router)

    assert api.requests.get(
        "/own", headers={"Authorization": "Bearer group"}
    ).status_code == 401
    assert api.requests.get(
        "/own", headers={"Authorization": "Bearer route"}
    ).status_code == 200


def test_included_routes_inherit_app_auth():
    api = _openapi_api(auth=BearerAuth(tokens=["t"]))
    router = Router()

    @router.get("/inherited")
    def inherited(req, resp, *, user):
        resp.media = {"user": user}

    api.include_router(router)

    assert api.requests.get("/inherited").status_code == 401
    assert api.requests.get(
        "/inherited", headers={"Authorization": "Bearer t"}
    ).json() == {"user": "t"}


# --- before_request hooks scoped to the router --------------------------------


def test_before_request_scoped_to_router(api):
    router = Router()

    @router.before_request()
    def require_key(req, resp):
        if "X-Api-Key" not in req.headers:
            resp.status_code = 401
            resp.media = {"error": "missing key"}

    @router.route("/data")
    def data(req, resp):
        resp.media = {"v": 1}

    api.include_router(router, prefix="/v1")

    @api.route("/public")
    def public(req, resp):
        resp.media = {"open": True}

    # "/v1x" shares the string prefix but is outside the router's subtree.
    @api.route("/v1x")
    def v1x(req, resp):
        resp.media = {"outside": True}

    assert api.requests.get("/v1/data").status_code == 401
    assert api.requests.get("/v1/data", headers={"X-Api-Key": "k"}).status_code == 200
    assert api.requests.get("/public").status_code == 200
    assert api.requests.get("/v1x").status_code == 200


def test_before_request_bare_decorator_and_async_hook(api):
    router = Router()
    seen = []

    @router.before_request
    async def observe(req, resp):
        seen.append(req.url.path)

    @router.route("/a")
    def a(req, resp):
        resp.text = "a"

    api.include_router(router, prefix="/scoped")

    @api.route("/outside")
    def outside(req, resp):
        resp.text = "o"

    api.requests.get("/scoped/a")
    api.requests.get("/outside")
    assert seen == ["/scoped/a"]


def test_before_request_async_callable_instance_hook_runs(api):
    # A hook instance with an async __call__ is not a coroutine *function*;
    # the prefix-scoping wrapper must still classify it as async, or the
    # guard's coroutine is created and discarded without ever running.
    class Guard:
        async def __call__(self, req, resp):
            resp.status_code = 401
            resp.media = {"error": "denied"}

    router = Router()
    router.add_route(None, Guard(), before_request=True)

    @router.route("/secret")
    def secret(req, resp):
        resp.media = {"v": 1}

    api.include_router(router, prefix="/v1")

    assert api.requests.get("/v1/secret").status_code == 401


# --- double inclusion ----------------------------------------------------------


def test_double_include_same_router_two_prefixes(api):
    router = Router()

    @router.route("/status")
    def status(req, resp):
        resp.media = {"ok": True}

    api.include_router(router, prefix="/v1")
    api.include_router(router, prefix="/v2")

    assert api.requests.get("/v1/status").json() == {"ok": True}
    assert api.requests.get("/v2/status").json() == {"ok": True}


def test_double_include_with_fresh_identical_metadata_is_allowed():
    # Fresh-but-identical Depends markers and auth instances must compare
    # equal, so including the same router twice with equivalent settings
    # works (as docs/source/routers.rst promises) instead of raising.
    api = _openapi_api()

    def check(req):
        pass

    router = Router()

    @router.route("/status")
    def status(req, resp):
        resp.media = {"ok": True}

    api.include_router(
        router,
        prefix="/v1",
        dependencies=[Depends(check)],
        auth=BearerAuth(tokens=["t"]),
    )
    api.include_router(
        router,
        prefix="/v2",
        dependencies=[Depends(check)],
        auth=BearerAuth(tokens=["t"]),
    )

    for prefix in ("/v1", "/v2"):
        assert api.requests.get(f"{prefix}/status").status_code == 401
        assert api.requests.get(
            f"{prefix}/status", headers={"Authorization": "Bearer t"}
        ).json() == {"ok": True}


def test_reinclude_with_different_metadata_raises(api):
    router = Router()

    @router.route("/status")
    def status(req, resp):
        resp.media = {"ok": True}

    api.include_router(router, prefix="/v1", tags=["a"])
    # Route metadata lives on the view function itself, so a second inclusion
    # with different tags/auth/dependencies would silently rewrite the first.
    with pytest.raises(ValueError, match="different"):
        api.include_router(router, prefix="/v2", tags=["b"])


def test_include_cannot_downgrade_directly_registered_auth():
    # A view registered directly via api.route(auth=strong) carries its auth
    # on the function itself; a later include_router() replaying the same
    # function with weaker auth would rewrite it in place, downgrading the
    # existing route. It must raise instead, leaving the strong auth intact.
    api = _openapi_api()
    strong = BearerAuth(tokens=["strong"])
    weak = BearerAuth(tokens=["weak"])

    @api.route("/admin", auth=strong)
    def admin(req, resp, *, user):
        resp.media = {"user": user}

    router = Router()
    router.add_route("/admin2", admin)

    with pytest.raises(ValueError, match="different"):
        api.include_router(router, auth=weak)

    # The direct route still enforces the original (strong) credential.
    assert api.requests.get(
        "/admin", headers={"Authorization": "Bearer weak"}
    ).status_code == 401
    assert api.requests.get(
        "/admin", headers={"Authorization": "Bearer strong"}
    ).json() == {"user": "strong"}


# --- conflicts and misuses ------------------------------------------------------


def test_include_conflicts_with_already_registered_route(api):
    @api.route("/v1/ping")
    def existing(req, resp):
        resp.text = "existing"

    router = Router()

    @router.route("/ping")
    def ping(req, resp):
        resp.text = "pong"

    with pytest.raises(ValueError, match="already exists"):
        api.include_router(router, prefix="/v1")


def test_inclusion_is_a_snapshot(api):
    router = Router()

    @router.route("/before")
    def before(req, resp):
        resp.text = "b"

    api.include_router(router)

    @router.route("/after")
    def after(req, resp):
        resp.text = "a"

    assert api.requests.get("/before").status_code == 200
    assert api.requests.get("/after").status_code == 404


def test_include_router_rejects_non_router(api):
    with pytest.raises(TypeError, match="responder.Router"):
        api.include_router(object())


def test_router_cannot_include_itself():
    router = Router()
    with pytest.raises(ValueError, match="cannot include itself"):
        router.include_router(router)


def test_router_include_router_rejects_non_router():
    router = Router()
    with pytest.raises(TypeError, match="responder.Router"):
        router.include_router("nope")


# --- api.group() keeps working (and its None-path bug is fixed) ----------------


def test_route_group_still_works(api):
    v1 = api.group("/v1")

    @v1.route("/users")
    def list_users(req, resp):
        resp.media = []

    assert api.requests.get("/v1/users").json() == []


def test_route_group_without_path_raises(api):
    g = api.group("/v1")

    with pytest.raises(ValueError, match="route path is required"):

        @g.route(None)
        def nameless(req, resp):
            pass

    # The old behavior registered a literal "/v1None" route.
    assert all("None" not in route.route for route in api.router.routes)
