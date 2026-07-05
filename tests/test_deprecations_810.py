"""v9 behavior defaults replacing the 8.1 deprecation paths."""

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


def test_session_accessor_removed(api):
    assert not hasattr(api, "session")


def test_requests_property_does_not_warn(api):
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        client = api.requests
        assert client is not None
        assert api.requests is client
    _assert_no_deprecation(recorded)


def test_test_client_accepts_custom_base_url_without_warning():
    api = responder.API(
        debug=False,
        allowed_hosts=["localhost"],
        static_dir=None,
        session_https_only=False,
    )

    @api.route("/")
    def index(req, resp):
        resp.text = req.url.hostname

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        client = api.test_client(base_url="http://localhost")
        assert client.get("/").text == "localhost"
    _assert_no_deprecation(recorded)


def test_test_client_accepts_starlette_options_without_warning():
    api = responder.API(
        debug=False, allowed_hosts=[";"], static_dir=None, session_https_only=False
    )

    @api.route("/boom")
    def boom(req, resp):
        raise RuntimeError("boom")

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        client = api.test_client(raise_server_exceptions=False)
        assert client.get("http://;/boom").status_code == 500
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


def test_serve_explicit_port_wins_when_port_and_env_conflict(
    api, fake_uvicorn, monkeypatch
):
    monkeypatch.setenv("PORT", "9000")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        api.serve(port=8000)
    _assert_no_deprecation(recorded)
    assert fake_uvicorn[0]["port"] == 8000
    # PORT still signals a platform-style bind unless address= is explicit.
    assert fake_uvicorn[0]["host"] == "0.0.0.0"  # noqa: S104


def test_run_explicit_port_wins_when_port_and_env_conflict(
    api, fake_uvicorn, monkeypatch
):
    monkeypatch.setenv("PORT", "9000")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        api.run(port=8000)
    _assert_no_deprecation(recorded)
    assert fake_uvicorn[0]["port"] == 8000


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


def test_serve_env_port_precedence_preserves_legacy_behavior(
    api, fake_uvicorn, monkeypatch
):
    monkeypatch.setenv("PORT", "9000")
    with pytest.warns(DeprecationWarning, match="PORT environment variable"):
        api.serve(port=8000, port_precedence="env")
    assert fake_uvicorn[0]["port"] == 9000
    assert fake_uvicorn[0]["host"] == "0.0.0.0"  # noqa: S104


def test_run_env_port_precedence_preserves_legacy_behavior(
    api, fake_uvicorn, monkeypatch
):
    monkeypatch.setenv("PORT", "9000")
    with pytest.warns(DeprecationWarning, match="PORT environment variable"):
        api.run(port=8000, port_precedence="env")
    assert fake_uvicorn[0]["port"] == 9000


def test_serve_rejects_unknown_port_precedence(api, fake_uvicorn):
    with pytest.raises(ValueError, match="port_precedence"):
        api.serve(port_precedence="command-line")
    assert fake_uvicorn == []


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


def test_bare_add_route_raises_by_default(static_api):
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="implicit_static_fallback=True"):
            static_api.add_route("/")
    _assert_no_deprecation(recorded)


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
        debug=False,
        allowed_hosts=[";"],
        static_dir=None,
        session_https_only=False,
        implicit_static_fallback=True,
    )
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="static_dir is disabled"):
            api.add_route("/")
    _assert_no_deprecation(recorded)


def test_bare_add_route_can_opt_into_legacy_fallback(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<h1>fallback</h1>")
    api = responder.API(
        debug=False,
        allowed_hosts=[";"],
        static_dir=str(static_dir),
        session_https_only=False,
        implicit_static_fallback=True,
    )
    with pytest.warns(DeprecationWarning, match="static-fallback"):
        api.add_route("/")
    r = api.requests.get("http://;/anything")
    assert r.status_code == 200
    assert "fallback" in r.text


# -----------------------------------------------------------------------
# 4. Decimal JSON serialization
# -----------------------------------------------------------------------


def test_json_default_decimal_serializes_as_string_without_warning(monkeypatch):
    monkeypatch.setattr(responder.formats, "_decimal_float_warned", False)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        assert responder.formats._json_default(Decimal("9.99")) == "9.99"
        assert responder.formats._json_default(Decimal("1.5")) == "1.5"
    _assert_no_deprecation(recorded)
    assert responder.formats._decimal_float_warned is False


def test_jsonable_decimal_uses_string_default(monkeypatch):
    monkeypatch.setattr(responder.formats, "_decimal_float_warned", False)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        assert responder.formats._jsonable({"price": Decimal("2.5")}) == {
            "price": "2.5"
        }
    _assert_no_deprecation(recorded)
    assert responder.formats._decimal_float_warned is False


def test_json_default_non_decimal_does_not_warn(monkeypatch):
    monkeypatch.setattr(responder.formats, "_decimal_float_warned", False)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        assert responder.formats._json_default({1, 2}) in ([1, 2], [2, 1])
    _assert_no_deprecation(recorded)
    assert responder.formats._decimal_float_warned is False


def test_decimal_media_end_to_end_serializes_as_string(api, monkeypatch):
    monkeypatch.setattr(responder.formats, "_decimal_float_warned", False)

    @api.route("/price")
    def price(req, resp):
        resp.media = {"price": Decimal("19.99")}

    r = api.requests.get("http://;/price")
    assert r.json() == {"price": "19.99"}
    assert responder.formats._decimal_float_warned is False


@pytest.mark.filterwarnings("ignore:Serializing decimal.Decimal:DeprecationWarning")
def test_decimal_float_mode_preserves_legacy_behavior_with_warning(monkeypatch):
    monkeypatch.setattr(responder.formats, "_decimal_float_warned", False)
    api = responder.API(
        debug=False,
        allowed_hosts=[";"],
        session_https_only=False,
        json_decimal="float",
    )

    @api.route("/price")
    def price(req, resp):
        resp.media = {"price": Decimal("19.99")}

    r = api.requests.get("http://;/price")
    assert r.json() == {"price": 19.99}
    assert responder.formats._decimal_float_warned is True


def test_json_decimal_rejects_unknown_mode():
    with pytest.raises(ValueError, match="json_decimal"):
        responder.API(
            debug=False,
            allowed_hosts=[";"],
            session_https_only=False,
            json_decimal="integer",
        )


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


def test_graphql_partial_data_200_by_default(api, partial_schema, monkeypatch):
    monkeypatch.setattr(graphql_ext, "_partial_data_400_warned", False)
    api.add_route("/gql", GraphQLView(schema=partial_schema, api=api))

    r = api.requests.post("http://;/gql", json={"query": "{ ok boom }"})
    assert r.status_code == 200
    data = r.json()
    assert data["data"] == {"ok": "fine", "boom": None}
    assert data["errors"]
    assert graphql_ext._partial_data_400_warned is False


@pytest.mark.filterwarnings("ignore:Returning HTTP 400 for GraphQL:DeprecationWarning")
def test_graphql_partial_data_400_legacy_mode_warns(
    api, partial_schema, monkeypatch
):
    monkeypatch.setattr(graphql_ext, "_partial_data_400_warned", False)
    api.graphql("/gql", schema=partial_schema, partial_data_status=400)

    r = api.requests.post("http://;/gql", json={"query": "{ ok boom }"})
    assert r.status_code == 400
    data = r.json()
    assert data["data"] == {"ok": "fine", "boom": None}
    assert data["errors"]
    assert graphql_ext._partial_data_400_warned is True


def test_graphql_partial_data_status_rejects_unknown_value(api, partial_schema):
    with pytest.raises(ValueError, match="partial_data_status"):
        api.graphql("/gql", schema=partial_schema, partial_data_status=202)


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
