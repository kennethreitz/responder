"""v5.2: responder.ext.auth — Bearer/Basic/API-key + OpenAPI security schemes."""

import base64

import pytest
import yaml
from starlette.testclient import TestClient

import responder
from responder.ext.auth import APIKeyAuth, BasicAuth, BearerAuth, OptionalAuth, ScopedAuth


def _api(**kwargs):
    return responder.API(
        title="T", version="1", openapi="3.0.2", secret_key="x" * 32,
        allowed_hosts=[";"], session_https_only=False, **kwargs,
    )


def _client(api):
    return TestClient(api, base_url="http://;")


def _basic_header(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


# --- Bearer ---------------------------------------------------------------


def test_bearer_static_tokens_inject_principal():
    api = _api()
    auth = BearerAuth(tokens=["s3cret"])
    api.add_dependency("user", auth)

    @api.get("/me")
    async def me(req, resp, *, user):
        resp.media = {"user": user}

    client = _client(api)
    assert client.get("/me").status_code == 401
    assert client.get("/me").headers["www-authenticate"] == "Bearer"
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer s3cret"}).json() == {
        "user": "s3cret"
    }


def test_bearer_async_verify():
    api = _api()
    users = {"tok-abc": {"id": 1, "name": "alice"}}

    async def verify(token):
        return users.get(token)

    auth = BearerAuth(verify=verify)
    api.add_dependency("user", auth)

    @api.get("/me")
    async def me(req, resp, *, user):
        resp.media = user

    client = _client(api)
    assert client.get("/me", headers={"Authorization": "Bearer tok-abc"}).json()["name"] == "alice"
    assert client.get("/me", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_bearer_realm_in_challenge():
    auth = BearerAuth(tokens=["x"], realm="api")
    assert auth._challenge() == 'Bearer realm="api"'


def test_bearer_requires_verify_or_tokens():
    with pytest.raises(ValueError):
        BearerAuth()


# --- Basic ----------------------------------------------------------------


def test_basic_static_credentials():
    api = _api()
    auth = BasicAuth(credentials={"alice": "pw"})

    @api.get("/secret")
    async def secret(req, resp):
        resp.media = {"who": await auth(req)}

    client = _client(api)
    r = client.get("/secret")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == 'Basic realm="Restricted"'
    assert client.get("/secret", headers={"Authorization": _basic_header("alice", "pw")}).json() == {
        "who": "alice"
    }
    assert client.get(
        "/secret", headers={"Authorization": _basic_header("alice", "wrong")}
    ).status_code == 401
    assert client.get(
        "/secret", headers={"Authorization": _basic_header("bob", "pw")}
    ).status_code == 401


def test_basic_malformed_header_rejected():
    api = _api()
    auth = BasicAuth(credentials={"alice": "pw"})

    @api.get("/s")
    async def s(req, resp):
        resp.media = {"who": await auth(req)}

    client = _client(api)
    assert client.get("/s", headers={"Authorization": "Basic !!!notbase64"}).status_code == 401


# --- API key --------------------------------------------------------------


def test_api_key_header():
    api = _api()
    auth = APIKeyAuth(keys=["abc123"], name="X-API-Key")

    @api.get("/k")
    async def k(req, resp):
        resp.media = {"key": await auth(req)}

    client = _client(api)
    r = client.get("/k")
    assert r.status_code == 401
    assert "www-authenticate" not in r.headers  # no standard challenge for API keys
    assert client.get("/k", headers={"X-API-Key": "abc123"}).json() == {"key": "abc123"}


def test_api_key_from_query():
    api = _api()
    auth = APIKeyAuth(keys=["q-key"], name="api_key", location="query")

    @api.get("/k")
    async def k(req, resp):
        resp.media = {"key": await auth(req)}

    assert _client(api).get("/k?api_key=q-key").json() == {"key": "q-key"}


def test_auto_error_false_returns_none():
    api = _api()
    auth = BearerAuth(tokens=["x"], auto_error=False)

    @api.get("/maybe")
    async def maybe(req, resp):
        resp.media = {"principal": await auth(req)}

    assert _client(api).get("/maybe").json() == {"principal": None}


@pytest.mark.parametrize(
    ("auth", "header", "challenge"),
    [
        (BearerAuth(tokens=["secret"]), "Basic abc123", "Bearer"),
        (
            BasicAuth(credentials={"alice": "pw"}),
            "Bearer secret",
            'Basic realm="Restricted"',
        ),
    ],
)
def test_wrong_authorization_scheme_is_rejected(auth, header, challenge):
    api = _api(auth=auth)

    @api.get("/private")
    def private(req, resp, *, user):
        resp.media = {"user": user}

    response = _client(api).get("/private", headers={"Authorization": header})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == challenge


def test_auto_error_false_returns_none_for_malformed_credentials():
    api = _api()
    auth = BearerAuth(tokens=["secret"], auto_error=False)

    @api.get("/maybe")
    async def maybe(req, resp):
        resp.media = {"principal": await auth(req)}

    response = _client(api).get(
        "/maybe", headers={"Authorization": "Basic not-bearer"}
    )

    assert response.json() == {"principal": None}


# --- OpenAPI integration --------------------------------------------------


def test_register_and_per_route_security():
    api = _api()
    BearerAuth(tokens=["x"]).register(api)

    @api.get("/me", security=["bearerAuth"])
    async def me(req, resp):
        resp.media = {}

    @api.get("/open")
    async def open_(req, resp):
        resp.media = {}

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    assert spec["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert spec["paths"]["/me"]["get"]["security"] == [{"bearerAuth": []}]
    assert "security" not in spec["paths"]["/open"]["get"]


def test_default_security_applies_to_all_operations():
    api = _api()
    api.add_security_scheme(BearerAuth(tokens=["x"]), default=True)

    @api.get("/a")
    async def a(req, resp):
        resp.media = {}

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    assert spec["paths"]["/a"]["get"]["security"] == [{"bearerAuth": []}]


def test_app_auth_applies_to_routes_and_can_be_disabled():
    auth = BearerAuth(tokens=["secret"])
    api = _api(auth=auth)

    @api.get("/private")
    def private(req, resp, *, user):
        resp.media = {"user": user}

    @api.get("/public", auth=None)
    def public(req, resp):
        resp.media = {"public": True}

    client = _client(api)
    assert client.get("/private").status_code == 401
    assert client.get(
        "/private", headers={"Authorization": "Bearer secret"}
    ).json() == {"user": "secret"}
    assert client.get("/public").json() == {"public": True}

    spec = yaml.safe_load(client.get("/schema.yml").content)
    assert spec["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert spec["paths"]["/private"]["get"]["security"] == [{"bearerAuth": []}]
    assert spec["paths"]["/public"]["get"]["security"] == []


def test_scoped_auth_requires_scope_and_documents_requirement():
    def verify(token):
        users = {
            "admin": {"id": 1, "scopes": "items:read items:write"},
            "reader": {"id": 2, "scopes": ["items:read"]},
        }
        return users.get(token)

    auth = ScopedAuth(BearerAuth(verify=verify), scopes=["items:write"])
    api = _api(auth=auth)

    @api.get("/items")
    def items(req, resp, *, user):
        resp.media = {"id": user["id"], "scopes": sorted(req.state.scopes)}

    client = _client(api)
    assert client.get("/items").status_code == 401
    assert client.get(
        "/items", headers={"Authorization": "Bearer reader"}
    ).status_code == 403
    assert client.get("/items", headers={"Authorization": "Bearer admin"}).json() == {
        "id": 1,
        "scopes": ["items:read", "items:write"],
    }

    spec = yaml.safe_load(client.get("/schema.yml").content)
    assert spec["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert spec["paths"]["/items"]["get"]["security"] == [
        {"bearerAuth": ["items:write"]}
    ]


def test_scoped_auth_accepts_roles_alias():
    auth = ScopedAuth(BearerAuth(tokens=["admin"]), roles=["admin"])
    api = _api(auth=auth)

    @api.get("/admin")
    def admin(req, resp, *, user):
        resp.media = {"user": user}

    assert _client(api).get(
        "/admin", headers={"Authorization": "Bearer admin"}
    ).status_code == 403

    auth = ScopedAuth(
        BearerAuth(verify=lambda token: {"name": token, "roles": ["admin"]}),
        roles=["admin"],
    )
    api = _api(auth=auth)

    @api.get("/admin")
    def admin_with_role(req, resp, *, user):
        resp.media = {"user": user["name"]}

    assert _client(api).get(
        "/admin", headers={"Authorization": "Bearer alice"}
    ).json() == {"user": "alice"}


def test_requires_helper_builds_scoped_auth_and_chains():
    def verify(token):
        return {"admin": {"id": 1, "scopes": ["read", "write"]}}.get(token)

    bearer = BearerAuth(verify=verify)
    scoped = bearer.requires("read").requires("write")
    assert isinstance(scoped, ScopedAuth)
    assert scoped.required_scopes == ("read", "write")

    api = _api(auth=scoped)

    @api.get("/items")
    def items(req, resp, *, user):
        resp.media = {"id": user["id"]}

    client = _client(api)
    assert client.get(
        "/items", headers={"Authorization": "Bearer admin"}
    ).json() == {"id": 1}
    spec = yaml.safe_load(client.get("/schema.yml").content)
    assert spec["paths"]["/items"]["get"]["security"] == [
        {"bearerAuth": ["read", "write"]}
    ]


def test_requires_custom_extractor():
    bearer = BearerAuth(verify=lambda token: {"name": token, "perms": "x y"})
    scoped = bearer.requires("y", extractor=lambda p: p["perms"].split())
    api = _api(auth=scoped)

    @api.get("/x")
    def x(req, resp, *, user):
        resp.media = {"ok": True}

    assert _client(api).get(
        "/x", headers={"Authorization": "Bearer t"}
    ).json() == {"ok": True}


def test_add_security_scheme_requires_openapi():
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)
    with pytest.raises(RuntimeError):
        api.add_security_scheme("bearerAuth", {"type": "http", "scheme": "bearer"})


def test_optional_auth_allows_anonymous_and_documents_both_modes():
    auth = BearerAuth(tokens=["secret"]).optional()
    assert isinstance(auth, OptionalAuth)
    api = _api(auth=auth)

    @api.get("/maybe")
    def maybe(req, resp, *, user):
        resp.media = {"user": user}

    client = _client(api)
    assert client.get("/maybe").json() == {"user": None}
    assert client.get(
        "/maybe", headers={"Authorization": "Bearer secret"}
    ).json() == {"user": "secret"}
    assert client.get(
        "/maybe", headers={"Authorization": "Bearer nope"}
    ).status_code == 401

    spec = yaml.safe_load(client.get("/schema.yml").content)
    assert spec["paths"]["/maybe"]["get"]["security"] == [{}, {"bearerAuth": []}]


def test_optional_basic_auth_rejects_malformed_header():
    auth = BasicAuth(credentials={"alice": "pw"}).optional()
    api = _api(auth=auth)

    @api.get("/maybe")
    def maybe(req, resp, *, user):
        resp.media = {"user": user}

    client = _client(api)
    assert client.get("/maybe").json() == {"user": None}
    assert client.get(
        "/maybe", headers={"Authorization": _basic_header("alice", "pw")}
    ).json() == {"user": "alice"}
    response = client.get("/maybe", headers={"Authorization": "Basic !!!notbase64"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="Restricted"'


def test_optional_bearer_auth_rejects_wrong_scheme():
    auth = BearerAuth(tokens=["secret"]).optional()
    api = _api(auth=auth)

    @api.get("/maybe")
    def maybe(req, resp, *, user):
        resp.media = {"user": user}

    client = _client(api)
    assert client.get("/maybe").json() == {"user": None}

    response = client.get("/maybe", headers={"Authorization": "Basic abc123"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_optional_api_key_auth_rejects_wrong_key():
    auth = APIKeyAuth(keys=["secret"], name="X-API-Key").optional()
    api = _api(auth=auth)

    @api.get("/maybe")
    def maybe(req, resp, *, user):
        resp.media = {"user": user}

    client = _client(api)
    assert client.get("/maybe").json() == {"user": None}
    assert client.get("/maybe", headers={"X-API-Key": "secret"}).json() == {
        "user": "secret"
    }
    response = client.get("/maybe", headers={"X-API-Key": "bad"})

    assert response.status_code == 401
    assert "www-authenticate" not in response.headers


def test_optional_scoped_auth_allows_anonymous_but_enforces_scope():
    auth = BearerAuth(
        verify=lambda token: {"name": token, "scopes": ["read"]}
    ).requires("admin").optional()
    api = _api(auth=auth)

    @api.get("/admin")
    def admin(req, resp, *, user):
        resp.media = {"user": user["name"] if user else None}

    client = _client(api)
    assert client.get("/admin").json() == {"user": None}

    response = client.get("/admin", headers={"Authorization": "Bearer alice"})

    assert response.status_code == 403
    assert response.headers["www-authenticate"] == (
        'Bearer error="insufficient_scope", scope="admin"'
    )

    spec = yaml.safe_load(client.get("/schema.yml").content)
    assert spec["paths"]["/admin"]["get"]["security"] == [
        {},
        {"bearerAuth": ["admin"]},
    ]


def test_scoped_auth_challenge_names_missing_scope():
    auth = BearerAuth(verify=lambda token: {"scopes": []}).requires("admin")
    api = _api(auth=auth)

    @api.get("/admin")
    def admin(req, resp):
        resp.media = {}

    response = _client(api).get("/admin", headers={"Authorization": "Bearer t"})

    assert response.status_code == 403
    assert response.headers["www-authenticate"] == (
        'Bearer error="insufficient_scope", scope="admin"'
    )


def test_scoped_auth_realm_challenge_uses_comma_separator():
    auth = BearerAuth(
        verify=lambda token: {"scopes": []}, realm="api"
    ).requires("admin")
    api = _api(auth=auth)

    @api.get("/admin")
    def admin(req, resp):
        resp.media = {}

    response = _client(api).get("/admin", headers={"Authorization": "Bearer t"})

    assert response.status_code == 403
    assert response.headers["www-authenticate"] == (
        'Bearer realm="api", error="insufficient_scope", scope="admin"'
    )


def test_scoped_auth_challenge_lists_multiple_missing_scopes():
    auth = BearerAuth(
        verify=lambda token: {"scopes": ["read"]}, realm="api"
    ).requires("write", "delete")
    api = _api(auth=auth)

    @api.get("/admin")
    def admin(req, resp):
        resp.media = {}

    response = _client(api).get("/admin", headers={"Authorization": "Bearer t"})

    assert response.status_code == 403
    assert response.headers["www-authenticate"] == (
        'Bearer realm="api", error="insufficient_scope", scope="write delete"'
    )
