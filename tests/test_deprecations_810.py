"""8.1: DeprecationWarnings starting the 9.0 clock.

Each warning fires only in its deprecated path; behavior is unchanged
until Responder 9.0.
"""

import types
import warnings
from decimal import Decimal

import pytest

import responder
import responder.api
import responder.formats


def _assert_no_deprecation(recorded):
    assert not [w for w in recorded if issubclass(w.category, DeprecationWarning)]


# -----------------------------------------------------------------------
# 1. api.session() legacy test-client accessor
# -----------------------------------------------------------------------


def test_session_warns_deprecation(api):
    with pytest.warns(DeprecationWarning, match=r"api\.requests"):
        client = api.session()
    assert client is not None


def test_session_still_returns_working_client(api):
    @api.route("/hello")
    def hello(req, resp):
        resp.text = "hi"

    with pytest.warns(DeprecationWarning):
        client = api.session()
    assert client.get("http://;/hello").text == "hi"


def test_session_warns_per_call_and_keeps_cache(api):
    with pytest.warns(DeprecationWarning):
        first = api.session()
    with pytest.warns(DeprecationWarning):
        again = api.session()
    assert first is again


def test_requests_property_does_not_warn(api):
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        client = api.requests
        assert client is not None
        assert api.requests is client
    _assert_no_deprecation(recorded)


# -----------------------------------------------------------------------
# 2. serve()/run(): PORT env var overriding an explicit port=
# -----------------------------------------------------------------------


@pytest.fixture
def fake_uvicorn(monkeypatch):
    calls = []

    def run(app, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(responder.api, "uvicorn", types.SimpleNamespace(run=run))
    return calls


def test_serve_warns_when_port_and_env_conflict(api, fake_uvicorn, monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    with pytest.warns(DeprecationWarning, match="PORT environment variable"):
        api.serve(port=8000)
    # Behavior unchanged until 9.0: the environment variable still wins.
    assert fake_uvicorn[0]["port"] == 9000


def test_run_warns_when_port_and_env_conflict(api, fake_uvicorn, monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    with pytest.warns(DeprecationWarning, match="PORT environment variable"):
        api.run(port=8000)
    assert fake_uvicorn[0]["port"] == 9000


def test_serve_no_warning_with_env_port_only(api, fake_uvicorn, monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        api.serve()
    _assert_no_deprecation(recorded)
    assert fake_uvicorn[0]["port"] == 9000
    assert fake_uvicorn[0]["host"] == "0.0.0.0"  # noqa: S104


def test_serve_no_warning_when_port_matches_env(api, fake_uvicorn, monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        api.serve(port=9000)
    _assert_no_deprecation(recorded)
    assert fake_uvicorn[0]["port"] == 9000


def test_serve_no_warning_with_explicit_port_only(api, fake_uvicorn, monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        api.serve(port=8000)
    _assert_no_deprecation(recorded)
    assert fake_uvicorn[0]["port"] == 8000


# -----------------------------------------------------------------------
# 3. Bare add_route() implicit static-fallback behavior
# -----------------------------------------------------------------------


@pytest.fixture
def static_api(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<h1>fallback</h1>")
    return responder.API(
        debug=False,
        allowed_hosts=[";"],
        static_dir=str(static_dir),
        session_https_only=False,
    )


def test_bare_add_route_warns(static_api):
    with pytest.warns(DeprecationWarning, match="static-fallback"):
        static_api.add_route("/")


def test_bare_add_route_still_serves_static_fallback(static_api):
    with pytest.warns(DeprecationWarning):
        static_api.add_route("/")
    r = static_api.requests.get("http://;/anything")
    assert r.status_code == 200
    assert "fallback" in r.text


def test_add_route_with_endpoint_does_not_warn(api):
    def view(req, resp):
        resp.text = "ok"

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        api.add_route("/explicit", view)
    _assert_no_deprecation(recorded)
    assert api.requests.get("http://;/explicit").text == "ok"


def test_bare_add_route_without_static_dir_raises_without_warning(tmp_path):
    api = responder.API(
        debug=False, allowed_hosts=[";"], static_dir=None, session_https_only=False
    )
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="static_dir is disabled"):
            api.add_route("/")
    _assert_no_deprecation(recorded)


# -----------------------------------------------------------------------
# 4. Decimal-to-float lossy JSON serialization (once per process)
# -----------------------------------------------------------------------


def test_json_default_decimal_warns_once(monkeypatch):
    monkeypatch.setattr(responder.formats, "_decimal_float_warned", False)
    with pytest.warns(DeprecationWarning, match="Decimal"):
        assert responder.formats._json_default(Decimal("9.99")) == 9.99
    # Latched: the second serialization stays silent.
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        assert responder.formats._json_default(Decimal("1.5")) == 1.5
    _assert_no_deprecation(recorded)


def test_jsonable_decimal_warns(monkeypatch):
    monkeypatch.setattr(responder.formats, "_decimal_float_warned", False)
    with pytest.warns(DeprecationWarning, match="Decimal"):
        assert responder.formats._jsonable({"price": Decimal("2.5")}) == {"price": 2.5}


def test_json_default_non_decimal_does_not_warn(monkeypatch):
    monkeypatch.setattr(responder.formats, "_decimal_float_warned", False)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        assert responder.formats._json_default({1, 2}) in ([1, 2], [2, 1])
    _assert_no_deprecation(recorded)
    assert responder.formats._decimal_float_warned is False


def test_decimal_media_end_to_end_behavior_unchanged(api, monkeypatch):
    """resp.media with a Decimal still serializes as a float (and latches)."""
    monkeypatch.setattr(responder.formats, "_decimal_float_warned", False)

    @api.route("/price")
    def price(req, resp):
        resp.media = {"price": Decimal("19.99")}

    r = api.requests.get("http://;/price")
    assert r.json() == {"price": 19.99}
    # The deprecated path ran (warning fires in the client's app thread).
    assert responder.formats._decimal_float_warned is True


# -----------------------------------------------------------------------
# 5. GraphQL 400-with-partial-data responses (once per process)
# -----------------------------------------------------------------------

graphene = pytest.importorskip("graphene")

from responder.ext import graphql as graphql_ext  # noqa: E402
from responder.ext.graphql import GraphQLView  # noqa: E402


@pytest.fixture
def partial_schema():
    class Query(graphene.ObjectType):
        ok = graphene.String()
        boom = graphene.String()

        def resolve_ok(self, info):
            return "fine"

        def resolve_boom(self, info):
            raise RuntimeError("nope")

    return graphene.Schema(query=Query)


def test_graphql_partial_data_helper_warns_once(monkeypatch):
    monkeypatch.setattr(graphql_ext, "_partial_data_400_warned", False)
    with pytest.warns(DeprecationWarning, match="GraphQL-over-HTTP"):
        graphql_ext._warn_partial_data_400()
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        graphql_ext._warn_partial_data_400()
    _assert_no_deprecation(recorded)


def test_graphql_partial_data_400_fires_warning(api, partial_schema, monkeypatch):
    monkeypatch.setattr(graphql_ext, "_partial_data_400_warned", False)
    api.add_route("/gql", GraphQLView(schema=partial_schema, api=api))

    r = api.requests.post("http://;/gql", json={"query": "{ ok boom }"})
    # Behavior unchanged until 9.0: partial data still gets a 400 ...
    assert r.status_code == 400
    data = r.json()
    assert data["data"] == {"ok": "fine", "boom": None}
    assert data["errors"]
    # ... but the deprecated path warned (in the client's app thread).
    assert graphql_ext._partial_data_400_warned is True


def test_graphql_success_does_not_warn(api, partial_schema, monkeypatch):
    monkeypatch.setattr(graphql_ext, "_partial_data_400_warned", False)
    api.add_route("/gql", GraphQLView(schema=partial_schema, api=api))

    r = api.requests.post("http://;/gql", json={"query": "{ ok }"})
    assert r.status_code == 200
    assert r.json() == {"data": {"ok": "fine"}}
    assert graphql_ext._partial_data_400_warned is False


def test_graphql_total_failure_400_does_not_warn(api, partial_schema, monkeypatch):
    """errors with data=None keeps 400 in 9.0 too — no warning."""
    monkeypatch.setattr(graphql_ext, "_partial_data_400_warned", False)
    api.add_route("/gql", GraphQLView(schema=partial_schema, api=api))

    r = api.requests.post("http://;/gql", json={"query": "{ nonexistent }"})
    assert r.status_code == 400
    assert "data" not in r.json()
    assert graphql_ext._partial_data_400_warned is False
