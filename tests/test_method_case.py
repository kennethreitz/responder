"""v7: req.method is a plain uppercase str."""

import warnings

import pytest

import responder


@pytest.fixture
def make_api():
    def _make(**kwargs):
        kwargs.setdefault("allowed_hosts", [";"])
        kwargs.setdefault("session_https_only", False)
        return responder.API(**kwargs)

    return _make


def _method_view(api):
    @api.route("/")
    def view(req, resp):
        resp.media = {
            "method": str(req.method),
            "is_str": isinstance(req.method, str),
            "exact_str_type": type(req.method) is str,
        }

    @api.route("/cbv")
    class Thing:
        def on_get(self, req, resp):
            resp.text = "got"

        def on_post(self, req, resp):
            resp.text = "posted"


def test_method_is_uppercase(make_api):
    api = make_api()
    _method_view(api)
    r = api.requests.get("/")
    assert r.json() == {"method": "GET", "is_str": True, "exact_str_type": True}


def test_exact_uppercase_compare_does_not_warn(make_api):
    api = make_api()

    @api.route("/")
    def view(req, resp):
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning becomes an error
            resp.media = {"is_get": req.method == "GET", "is_post": req.method == "POST"}

    assert api.requests.get("/").json() == {"is_get": True, "is_post": False}


def test_lowercase_compare_is_false_without_warning(make_api):
    # v7: the case-insensitive shim is removed; comparison is plain str.
    api = make_api()
    seen = {}

    @api.route("/")
    def view(req, resp):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            eq = req.method == "get"  # case-sensitive miss
            member = req.method in ("get", "head")  # case-sensitive miss
        seen["eq"] = eq
        seen["member"] = member
        seen["warnings"] = sum(
            issubclass(w.category, DeprecationWarning) for w in caught
        )
        resp.text = "ok"

    api.requests.get("/")
    assert seen["eq"] is False
    assert seen["member"] is False
    assert seen["warnings"] == 0


def test_method_lower_is_plain_str(make_api):
    api = make_api()

    @api.route("/")
    def view(req, resp):
        m = req.method.lower()
        resp.media = {"lower": m, "exact_str_type": type(m) is str, "eq": m == "get"}

    assert api.requests.get("/").json() == {
        "lower": "get",
        "exact_str_type": True,
        "eq": True,
    }


def test_method_membership_is_plain_case_sensitive_str(make_api):
    # Uppercase set membership works; lowercase misses.
    api = make_api()

    @api.route("/")
    def view(req, resp):
        resp.media = {
            "upper_in": req.method in {"GET", "POST"},
            "lower_in": req.method in {"get"},
            "exact_str_type": type(req.method) is str,
        }

    assert api.requests.get("/").json() == {
        "upper_in": True,
        "lower_in": False,
        "exact_str_type": True,
    }


def test_class_based_view_dispatch_still_works(make_api):
    api = make_api()
    _method_view(api)
    assert api.requests.get("/cbv").text == "got"
    assert api.requests.post("/cbv").text == "posted"
