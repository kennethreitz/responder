"""v5.3: PEP 593 Annotated[...] support for Query/Header/Cookie/Path markers."""

from typing import Annotated

import yaml
from starlette.testclient import TestClient

import responder
from responder import Header, Query


def _api():
    return responder.API(
        title="T", version="1", openapi="3.0.2",
        allowed_hosts=[";"], session_https_only=False,
    )


def _client(api):
    return TestClient(api, base_url="http://;")


def test_annotated_query_required_and_constrained():
    api = _api()

    @api.route("/s")
    def s(req, resp, *, q: Annotated[str, Query(..., min_length=3)]):
        resp.media = {"q": q}

    client = _client(api)
    assert client.get("/s?q=abcd").json() == {"q": "abcd"}
    assert client.get("/s").status_code == 422  # required
    assert client.get("/s?q=ab").status_code == 422  # min_length


def test_annotated_query_with_default_and_coercion():
    api = _api()

    @api.route("/n")
    def n(req, resp, *, limit: Annotated[int, Query(10, ge=1, le=100)] = 10):
        resp.media = {"limit": limit}

    client = _client(api)
    assert client.get("/n").json() == {"limit": 10}
    assert client.get("/n?limit=5").json() == {"limit": 5}
    assert client.get("/n?limit=0").status_code == 422
    assert client.get("/n?limit=999").status_code == 422


def test_annotated_header():
    api = _api()

    @api.route("/h")
    def h(req, resp, *, token: Annotated[str, Header(None)] = None):
        resp.media = {"token": token}

    assert _client(api).get("/h", headers={"token": "xyz"}).json() == {"token": "xyz"}


def test_optional_wrapped_marker_is_detected():
    # Python <=3.10's get_type_hints wraps `x: T = None` in Optional, hiding the
    # Annotated metadata behind a Union. Detection must see through it. (This
    # exercises the shape on every interpreter, not just 3.10.)
    from typing import Optional

    from responder.params import _annotated_marker

    assert _annotated_marker(Annotated[str, Header(None)]) is not None
    assert _annotated_marker(Optional[Annotated[str, Header(None)]]) is not None
    assert _annotated_marker(str) is None


def test_annotated_marker_appears_in_openapi():
    api = _api()

    @api.route("/s")
    def s(req, resp, *, q: Annotated[str, Query(..., min_length=3)]):
        resp.media = {}

    item = yaml.safe_load(_client(api).get("/schema.yml").content)["paths"]["/s"]
    params = {
        p["name"]: p
        for p in item.get("parameters", []) + item["get"].get("parameters", [])
    }
    assert params["q"]["in"] == "query"
    assert params["q"]["required"] is True
    assert params["q"]["schema"]["minLength"] == 3
