"""v8.1: responder.ext.auth — JWTAuth and OAuth2 bearer flows."""

import asyncio
import base64
import http.server
import json
import sys
import threading
import time

import pytest
import yaml
from starlette.testclient import TestClient

import responder
from responder.ext.auth import (
    JWTAuth,
    OAuth2Auth,
    OAuth2AuthorizationCodeFlow,
    OAuth2ClientCredentialsFlow,
    OAuth2PasswordFlow,
    _default_scopes,
)

pyjwt = pytest.importorskip("jwt", reason="PyJWT is required for JWT auth tests")

# 32+ bytes so PyJWT does not warn about weak HS256 keys.
SECRET = "unit-test-secret-0123456789abcdef"
AUDIENCE = "https://api.example.com"
ISSUER = "https://issuer.example.com"
TOKEN_URL = "https://issuer.example.com/oauth/token"
AUTHZ_URL = "https://issuer.example.com/authorize"


def _api(**kwargs):
    return responder.API(
        title="T", version="1", openapi="3.0.2", secret_key="x" * 32,
        allowed_hosts=[";"], session_https_only=False, **kwargs,
    )


def _client(api):
    return TestClient(api, base_url="http://;")


def _token(secret=SECRET, alg="HS256", headers=None, **claims):
    return pyjwt.encode(claims, secret, algorithm=alg, headers=headers)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _me_app(auth):
    api = _api()

    @api.get("/me", auth=auth)
    async def me(req, resp, *, user):
        resp.media = {"claims": user}

    return _client(api)


# --- JWTAuth: construction ---------------------------------------------------


def test_jwt_requires_secret_or_jwks_url():
    with pytest.raises(ValueError, match="secret= or jwks_url="):
        JWTAuth()
    with pytest.raises(ValueError, match="not both"):
        JWTAuth(SECRET, jwks_url="https://issuer/jwks.json")


def test_jwt_security_scheme_declares_bearer_jwt():
    assert JWTAuth(SECRET).security_scheme() == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }


def test_jwt_value_equality_ignores_lazy_jwks_client():
    one = JWTAuth(SECRET, audience=AUDIENCE, issuer=ISSUER)
    two = JWTAuth(SECRET, audience=AUDIENCE, issuer=ISSUER)
    assert one == two
    assert one != JWTAuth("another-secret-0123456789abcdefgh")
    assert one != OAuth2Auth.password(TOKEN_URL, jwt=two)
    # A lazily built JWKS client is transport state, not configuration.
    one._jwks_client = object()
    assert one == two
    assert hash(one) == hash(two)


def test_jwt_missing_pyjwt_raises_helpful_import_error(monkeypatch):
    auth = JWTAuth(SECRET)
    monkeypatch.setitem(sys.modules, "jwt", None)  # force `import jwt` to fail
    with pytest.raises(ImportError, match=r"responder\[jwt\]"):
        asyncio.run(auth._verify("token"))


# --- JWTAuth: signature and claims validation --------------------------------


def test_jwt_valid_token_injects_claims():
    client = _me_app(JWTAuth(SECRET))
    token = _token(sub="alice")
    response = client.get("/me", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json()["claims"]["sub"] == "alice"


def test_jwt_missing_token_is_401_with_bearer_challenge():
    client = _me_app(JWTAuth(SECRET))
    response = client.get("/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_jwt_realm_appears_in_challenge():
    client = _me_app(JWTAuth(SECRET, realm="api"))
    assert _me_app(JWTAuth(SECRET, realm="api")).get("/me").headers[
        "www-authenticate"
    ] == 'Bearer realm="api"'
    assert client.get("/me", headers=_bearer("garbage")).status_code == 401


def test_jwt_bad_signature_is_401():
    client = _me_app(JWTAuth(SECRET))
    forged = _token(secret="wrong-secret-0123456789abcdefghij", sub="mallory")
    assert client.get("/me", headers=_bearer(forged)).status_code == 401


def test_jwt_disallowed_algorithm_is_401():
    client = _me_app(JWTAuth(SECRET, algorithms=("HS256",)))
    hs512 = _token(secret=SECRET * 2, alg="HS512", sub="alice")
    assert client.get("/me", headers=_bearer(hs512)).status_code == 401


def test_jwt_expired_token_is_401_and_leeway_allows_skew():
    just_expired = _token(sub="alice", exp=int(time.time()) - 5)
    strict = _me_app(JWTAuth(SECRET))
    assert strict.get("/me", headers=_bearer(just_expired)).status_code == 401
    lenient = _me_app(JWTAuth(SECRET, leeway=60))
    assert lenient.get("/me", headers=_bearer(just_expired)).status_code == 200


def test_jwt_not_yet_valid_token_is_401_and_leeway_allows_skew():
    premature = _token(sub="alice", nbf=int(time.time()) + 30)
    strict = _me_app(JWTAuth(SECRET))
    assert strict.get("/me", headers=_bearer(premature)).status_code == 401
    lenient = _me_app(JWTAuth(SECRET, leeway=60))
    assert lenient.get("/me", headers=_bearer(premature)).status_code == 200


def test_jwt_audience_is_enforced():
    client = _me_app(JWTAuth(SECRET, audience=AUDIENCE))
    wrong = _token(sub="alice", aud="https://other.example.com")
    assert client.get("/me", headers=_bearer(wrong)).status_code == 401
    missing = _token(sub="alice")
    assert client.get("/me", headers=_bearer(missing)).status_code == 401
    right = _token(sub="alice", aud=AUDIENCE)
    assert client.get("/me", headers=_bearer(right)).status_code == 200


def test_jwt_issuer_is_enforced():
    client = _me_app(JWTAuth(SECRET, issuer=ISSUER))
    wrong = _token(sub="alice", iss="https://rogue.example.com")
    assert client.get("/me", headers=_bearer(wrong)).status_code == 401
    right = _token(sub="alice", iss=ISSUER)
    assert client.get("/me", headers=_bearer(right)).status_code == 200


def test_jwt_decode_options_pass_through():
    client = _me_app(JWTAuth(SECRET, options={"require": ["exp"]}))
    no_exp = _token(sub="alice")
    assert client.get("/me", headers=_bearer(no_exp)).status_code == 401
    with_exp = _token(sub="alice", exp=int(time.time()) + 60)
    assert client.get("/me", headers=_bearer(with_exp)).status_code == 200


def test_jwt_rs256_with_pem_public_key():
    pytest.importorskip("cryptography", reason="RS256 requires cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    client = _me_app(JWTAuth(public_pem, algorithms=("RS256",)))
    token = pyjwt.encode({"sub": "alice"}, private_pem, algorithm="RS256")
    response = client.get("/me", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json()["claims"] == {"sub": "alice"}

    # Algorithm-confusion hardening: an HS256 token HMAC-signed with the
    # *public* key bytes must not sneak through — even when HS256 is also in
    # the allowed algorithms (PyJWT refuses PEM keys as HMAC secrets). PyJWT
    # itself declines to mint such a token, so craft it by hand.
    import hashlib
    import hmac

    def b64url(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    signing_input = (
        b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        + b"."
        + b64url(json.dumps({"sub": "mallory"}).encode())
    )
    signature = b64url(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    confused = (signing_input + b"." + signature).decode()
    assert client.get("/me", headers=_bearer(confused)).status_code == 401
    permissive = _me_app(JWTAuth(public_pem, algorithms=("RS256", "HS256")))
    assert permissive.get("/me", headers=_bearer(confused)).status_code == 401


# --- JWTAuth: verify callback -------------------------------------------------


def test_jwt_verify_callback_maps_claims_to_principal():
    async def verify(claims):
        if claims["sub"] != "alice":
            return None
        return {"id": 1, "name": claims["sub"]}

    client = _me_app(JWTAuth(SECRET, verify=verify))
    ok = client.get("/me", headers=_bearer(_token(sub="alice")))
    assert ok.json()["claims"] == {"id": 1, "name": "alice"}
    rejected = client.get("/me", headers=_bearer(_token(sub="bob")))
    assert rejected.status_code == 401


# --- JWTAuth: scopes ----------------------------------------------------------


def test_default_scopes_ignores_raw_oauth2_scope_claim():
    # The generic extractor consults only ``scopes``/``roles`` — the raw
    # OAuth2 ``scope`` claim is normalized into ``scopes`` at the JWT layer
    # (see _normalize_scope_claims) and must not be read here, or it would
    # shadow ``roles`` and change scope semantics for every non-JWT scheme.
    assert _default_scopes({"scope": "read write"}) == frozenset()
    assert _default_scopes({"scopes": ["a"], "scope": "b"}) == {"a"}
    assert _default_scopes({"roles": ["admin"], "scope": "openid"}) == {"admin"}
    assert _default_scopes({"sub": "alice"}) == frozenset()


def test_jwt_scope_claim_feeds_requires():
    auth = JWTAuth(SECRET)
    api = _api()

    @api.get("/read", auth=auth.requires("read"))
    async def read(req, resp, *, user):
        resp.media = {"sub": user["sub"]}

    @api.get("/admin", auth=auth.requires("admin"))
    async def admin(req, resp, *, user):
        resp.media = {"sub": user["sub"]}

    client = _client(api)
    token = _token(sub="alice", scope="read write")
    assert client.get("/read", headers=_bearer(token)).status_code == 200
    denied = client.get("/admin", headers=_bearer(token))
    assert denied.status_code == 403
    challenge = denied.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="admin"' in challenge


def test_jwt_scopes_list_claim_feeds_requires():
    auth = JWTAuth(SECRET)
    api = _api()

    @api.get("/write", auth=auth.requires("write"))
    async def write(req, resp, *, user):
        resp.media = {"ok": True}

    client = _client(api)
    token = _token(sub="alice", scopes=["read", "write"])
    assert client.get("/write", headers=_bearer(token)).status_code == 200


# --- JWTAuth: JWKS ------------------------------------------------------------


class _JWKS:
    """A tiny in-process JWKS endpoint with swappable keys and a hit counter."""

    def __init__(self):
        self.keys = []
        self.hits = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                outer.hits += 1
                body = json.dumps({"keys": outer.keys}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep test output quiet
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.url = f"http://{host}:{port}/.well-known/jwks.json"

    def add_key(self, kid, secret):
        k = base64.urlsafe_b64encode(secret.encode()).rstrip(b"=").decode()
        self.keys.append(
            {"kty": "oct", "kid": kid, "alg": "HS256", "use": "sig", "k": k}
        )

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def jwks():
    endpoint = _JWKS()
    yield endpoint
    endpoint.close()


def test_jwt_jwks_url_resolves_caches_and_refreshes_keys(jwks):
    key_one = "jwks-secret-one-0123456789abcdef"
    jwks.add_key("k1", key_one)
    client = _me_app(JWTAuth(jwks_url=jwks.url))

    token = _token(secret=key_one, headers={"kid": "k1"}, sub="alice")
    assert client.get("/me", headers=_bearer(token)).status_code == 200
    assert jwks.hits == 1

    # The key set is cached: a second request does not refetch.
    assert client.get("/me", headers=_bearer(token)).status_code == 200
    assert jwks.hits == 1

    # Key rotation: an unknown kid triggers a JWKS refresh.
    key_two = "jwks-secret-two-0123456789abcdef"
    jwks.add_key("k2", key_two)
    rotated = _token(secret=key_two, headers={"kid": "k2"}, sub="alice")
    assert client.get("/me", headers=_bearer(rotated)).status_code == 200
    assert jwks.hits == 2

    # A kid the endpoint never serves stays a clean 401, not a 500.
    unknown = _token(secret=key_one, headers={"kid": "nope"}, sub="alice")
    assert client.get("/me", headers=_bearer(unknown)).status_code == 401


def test_jwt_unreachable_jwks_endpoint_is_401_not_500():
    client = _me_app(JWTAuth(jwks_url="http://127.0.0.1:1/jwks.json"))
    token = _token(headers={"kid": "k1"}, sub="alice")
    assert client.get("/me", headers=_bearer(token)).status_code == 401


def test_jwt_roles_and_scope_claims_coexist():
    # Regression (finding 0, lockout scenario): a JWT carrying authorization in
    # ``roles`` plus an unrelated OAuth2 ``scope`` string must grant BOTH — the
    # normalized ``scope`` claim must never shadow ``roles`` into a 403 lockout.
    auth = JWTAuth(SECRET)
    api = _api()

    @api.get("/role", auth=auth.requires("admin"))
    async def role(req, resp, *, user):
        resp.media = {"ok": True}

    @api.get("/scope", auth=auth.requires("profile"))
    async def scope(req, resp, *, user):
        resp.media = {"ok": True}

    client = _client(api)
    token = _token(sub="alice", roles=["admin"], scope="openid profile")
    assert client.get("/role", headers=_bearer(token)).status_code == 200
    assert client.get("/scope", headers=_bearer(token)).status_code == 200


def test_jwt_scp_claim_feeds_requires():
    # Azure AD / Auth0 spell the scope claim ``scp`` (string or list).
    auth = JWTAuth(SECRET)
    api = _api()

    @api.get("/read", auth=auth.requires("read"))
    async def read(req, resp, *, user):
        resp.media = {"ok": True}

    client = _client(api)
    str_token = _token(sub="alice", scp="read write")
    assert client.get("/read", headers=_bearer(str_token)).status_code == 200
    list_token = _token(sub="alice", scp=["read", "write"])
    assert client.get("/read", headers=_bearer(list_token)).status_code == 200


def test_jwt_jwks_lookup_does_not_block_event_loop():
    # Regression (findings 1 + 16): resolving the JWKS signing key does
    # blocking urllib I/O; an attacker sending random ``kid`` values triggers a
    # network refetch with a multi-second timeout. That work must run off the
    # event loop, so a concurrent coroutine keeps making progress while a slow
    # JWKS lookup is in flight.
    auth = JWTAuth(jwks_url="http://127.0.0.1:1/jwks.json")
    slow_token = _token(secret=SECRET, headers={"kid": "slow"}, sub="alice")

    block_seconds = 0.5
    started = threading.Event()

    def slow_signing_key(token):
        # Simulate PyJWKClient.get_signing_key_from_jwt() doing blocking I/O.
        started.set()
        time.sleep(block_seconds)
        raise pyjwt.PyJWTError("unreachable JWKS")

    auth._signing_key = slow_signing_key

    async def scenario():
        order = []

        async def other_work():
            # Wait until the blocking signing-key call is definitely running,
            # then do a quick bit of async work. If the loop were frozen this
            # coroutine could not even start until the 0.5s block finished.
            while not started.is_set():
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)
            order.append("other")

        async def verify_work():
            await auth._verify(slow_token)
            order.append("verify")

        await asyncio.wait_for(
            asyncio.gather(verify_work(), other_work()),
            timeout=block_seconds * 3,
        )

        # The concurrent coroutine must finish BEFORE the slow verify — only
        # possible if the blocking JWKS lookup ran off the event loop.
        assert order == ["other", "verify"], order

    asyncio.run(scenario())


# --- OAuth2 flow descriptions ---------------------------------------------------


def test_oauth2_flow_specs():
    code = OAuth2AuthorizationCodeFlow(
        AUTHZ_URL,
        TOKEN_URL,
        refresh_url="https://issuer.example.com/refresh",
        scopes={"read": "Read access"},
    )
    assert code.flow_name == "authorizationCode"
    assert code.spec() == {
        "authorizationUrl": AUTHZ_URL,
        "tokenUrl": TOKEN_URL,
        "refreshUrl": "https://issuer.example.com/refresh",
        "scopes": {"read": "Read access"},
    }

    machine = OAuth2ClientCredentialsFlow(TOKEN_URL, scopes=["svc"])
    assert machine.flow_name == "clientCredentials"
    assert machine.spec() == {"tokenUrl": TOKEN_URL, "scopes": {"svc": ""}}

    password = OAuth2PasswordFlow(TOKEN_URL, scopes="read write")
    assert password.flow_name == "password"
    assert password.spec() == {
        "tokenUrl": TOKEN_URL,
        "scopes": {"read": "", "write": ""},
    }


def test_oauth2_flow_value_equality():
    assert OAuth2PasswordFlow(TOKEN_URL, scopes="a") == OAuth2PasswordFlow(
        TOKEN_URL, scopes=["a"]
    )
    assert OAuth2PasswordFlow(TOKEN_URL) != OAuth2ClientCredentialsFlow(TOKEN_URL)


# --- OAuth2Auth: construction and OpenAPI ---------------------------------------


def test_oauth2_requires_flows_and_exactly_one_validator():
    flow = OAuth2PasswordFlow(TOKEN_URL)
    with pytest.raises(ValueError, match="jwt= or verify="):
        OAuth2Auth(flow)
    with pytest.raises(ValueError, match="not both"):
        OAuth2Auth(flow, jwt=JWTAuth(SECRET), verify=lambda token: token)
    with pytest.raises(ValueError, match="at least one flow"):
        OAuth2Auth([], verify=lambda token: token)
    with pytest.raises(TypeError, match="OAuth2Flow"):
        OAuth2Auth("password", verify=lambda token: token)
    with pytest.raises(ValueError, match="distinct"):
        OAuth2Auth(
            [OAuth2PasswordFlow(TOKEN_URL), OAuth2PasswordFlow(TOKEN_URL)],
            verify=lambda token: token,
        )


def test_oauth2_security_scheme_emits_flows_and_scopes():
    auth = OAuth2Auth(
        [
            OAuth2AuthorizationCodeFlow(
                AUTHZ_URL, TOKEN_URL, scopes={"read": "Read access"}
            ),
            OAuth2ClientCredentialsFlow(TOKEN_URL, scopes={"svc": "Service"}),
        ],
        verify=lambda token: token,
        description="Sign in via the example issuer.",
    )
    assert auth.security_scheme() == {
        "type": "oauth2",
        "description": "Sign in via the example issuer.",
        "flows": {
            "authorizationCode": {
                "authorizationUrl": AUTHZ_URL,
                "tokenUrl": TOKEN_URL,
                "scopes": {"read": "Read access"},
            },
            "clientCredentials": {
                "tokenUrl": TOKEN_URL,
                "scopes": {"svc": "Service"},
            },
        },
    }


def test_oauth2_scheme_lands_in_openapi_document_with_scoped_requirement():
    auth = OAuth2Auth.authorization_code(
        AUTHZ_URL,
        TOKEN_URL,
        scopes={"read": "Read access", "write": "Write access"},
        jwt=JWTAuth(SECRET, audience=AUDIENCE),
    )
    api = _api()

    @api.get("/items", auth=auth.requires("read"))
    async def items(req, resp, *, user):
        resp.media = []

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    scheme = spec["components"]["securitySchemes"]["oauth2Auth"]
    assert scheme["type"] == "oauth2"
    assert scheme["flows"]["authorizationCode"] == {
        "authorizationUrl": AUTHZ_URL,
        "tokenUrl": TOKEN_URL,
        "scopes": {"read": "Read access", "write": "Write access"},
    }
    assert spec["paths"]["/items"]["get"]["security"] == [{"oauth2Auth": ["read"]}]


# --- OAuth2Auth: runtime enforcement --------------------------------------------


def test_oauth2_password_flow_delegates_to_jwtauth():
    auth = OAuth2Auth.password(
        TOKEN_URL,
        scopes={"read": "Read access"},
        jwt=JWTAuth(SECRET, audience=AUDIENCE),
        realm="api",
    )
    api = _api()

    @api.get("/items", auth=auth.requires("read"))
    async def items(req, resp, *, user):
        resp.media = {"sub": user["sub"]}

    client = _client(api)
    anonymous = client.get("/items")
    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"] == 'Bearer realm="api"'

    good = _token(sub="alice", aud=AUDIENCE, scope="read")
    assert client.get("/items", headers=_bearer(good)).json() == {"sub": "alice"}

    wrong_aud = _token(sub="alice", aud="https://other", scope="read")
    assert client.get("/items", headers=_bearer(wrong_aud)).status_code == 401

    no_scope = _token(sub="alice", aud=AUDIENCE, scope="write")
    assert client.get("/items", headers=_bearer(no_scope)).status_code == 403


def test_oauth2_verify_callable_acts_as_token_introspection():
    tokens = {"opaque-token": {"sub": "svc-1", "scope": "svc"}}

    async def introspect(token):
        return tokens.get(token)

    auth = OAuth2Auth.client_credentials(
        TOKEN_URL, scopes={"svc": "Service"}, verify=introspect
    )
    api = _api()

    @api.get("/svc", auth=auth.requires("svc"))
    async def svc(req, resp, *, user):
        resp.media = {"sub": user["sub"]}

    client = _client(api)
    assert client.get("/svc").status_code == 401
    assert client.get("/svc", headers=_bearer("bogus")).status_code == 401
    assert client.get("/svc", headers=_bearer("opaque-token")).json() == {
        "sub": "svc-1"
    }


def test_oauth2_optional_allows_anonymous():
    auth = OAuth2Auth.password(TOKEN_URL, jwt=JWTAuth(SECRET))
    api = _api()

    @api.get("/maybe", auth=auth.optional())
    async def maybe(req, resp, *, user):
        resp.media = {"user": user}

    client = _client(api)
    assert client.get("/maybe").json() == {"user": None}
    token = _token(sub="alice")
    assert client.get("/maybe", headers=_bearer(token)).json()["user"]["sub"] == "alice"
    assert client.get("/maybe", headers=_bearer("garbage")).status_code == 401


# --- Group-level auth via Router / include_router --------------------------------


def test_router_group_auth_with_jwtauth():
    router = responder.Router(prefix="/v1", auth=JWTAuth(SECRET, audience=AUDIENCE))

    @router.route("/me")
    async def me(req, resp, *, user):
        resp.media = {"sub": user["sub"]}

    api = _api()
    api.include_router(router)

    client = _client(api)
    assert client.get("/v1/me").status_code == 401
    token = _token(sub="alice", aud=AUDIENCE)
    assert client.get("/v1/me", headers=_bearer(token)).json() == {"sub": "alice"}

    spec = yaml.safe_load(client.get("/schema.yml").content)
    assert spec["components"]["securitySchemes"]["jwtAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert spec["paths"]["/v1/me"]["get"]["security"] == [{"jwtAuth": []}]


def test_router_reinclusion_with_fresh_identical_jwtauth_objects():
    router = responder.Router()

    @router.route("/who")
    async def who(req, resp, *, user):
        resp.media = {"sub": user["sub"]}

    api = _api()
    # Fresh-but-identical JWTAuth instances must not trip the re-inclusion
    # guard (value equality via the _ValueEqual convention).
    api.include_router(router, prefix="/a", auth=JWTAuth(SECRET))
    api.include_router(router, prefix="/b", auth=JWTAuth(SECRET))

    client = _client(api)
    token = _token(sub="alice")
    for prefix in ("/a", "/b"):
        assert client.get(f"{prefix}/who").status_code == 401
        assert client.get(f"{prefix}/who", headers=_bearer(token)).json() == {
            "sub": "alice"
        }


def test_router_group_auth_with_oauth2auth():
    auth = OAuth2Auth.client_credentials(
        TOKEN_URL, scopes={"svc": "Service"}, jwt=JWTAuth(SECRET)
    )
    router = responder.Router(prefix="/svc", auth=auth)

    @router.route("/status")
    async def status(req, resp, *, user):
        resp.media = {"ok": True}

    api = _api()
    api.include_router(router)

    client = _client(api)
    assert client.get("/svc/status").status_code == 401
    token = _token(sub="svc-1")
    assert client.get("/svc/status", headers=_bearer(token)).json() == {"ok": True}

    spec = yaml.safe_load(client.get("/schema.yml").content)
    assert spec["components"]["securitySchemes"]["oauth2Auth"]["type"] == "oauth2"
    assert spec["paths"]["/svc/status"]["get"]["security"] == [{"oauth2Auth": []}]
