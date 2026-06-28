"""v5.1: Query/Header/Cookie/Path markers honor constraints and reject typos."""

import pytest
import yaml
from starlette.testclient import TestClient

import responder
from responder import Query


def _api():
    return responder.API(
        title="T", version="1", openapi="3.0.2",
        allowed_hosts=[";"], session_https_only=False,
    )


def _client(api):
    return TestClient(api, base_url="http://;")


def test_unknown_kwarg_is_rejected():
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        Query("x", dafault=5)


def test_string_constraint_enforced():
    api = _api()

    @api.route("/s")
    def search(req, resp, *, q: str = Query("hello", min_length=3)):
        resp.media = {"q": q}

    client = _client(api)
    assert client.get("/s?q=abcd").status_code == 200
    assert client.get("/s?q=ab").status_code == 422


def test_numeric_constraint_enforced():
    api = _api()

    @api.route("/n")
    def num(req, resp, *, n: int = Query(1, ge=1, le=10)):
        resp.media = {"n": n}

    client = _client(api)
    assert client.get("/n?n=5").status_code == 200
    assert client.get("/n?n=0").status_code == 422
    assert client.get("/n?n=11").status_code == 422


def test_constraints_and_metadata_in_openapi():
    api = _api()

    @api.route("/s")
    def search(
        req,
        resp,
        *,
        q: str = Query("x", min_length=3, description="search term", deprecated=True),
    ):
        resp.media = {"q": q}

    item = yaml.safe_load(_client(api).get("/schema.yml").content)["paths"]["/s"]
    params = item.get("parameters", []) + item["get"].get("parameters", [])
    q = next(p for p in params if p["name"] == "q")
    assert q["schema"]["minLength"] == 3
    assert q["description"] == "search term"
    assert q["deprecated"] is True
