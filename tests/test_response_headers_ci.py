"""Response.headers is case-insensitive and case-preserving."""

import copy
import pickle

from responder.models import CaseInsensitiveDict


def test_handler_content_type_overrides_framework_spelling(api):
    # Setting 'content-type' must replace the framework's 'Content-Type',
    # not emit a second header on the wire.
    @api.route("/")
    def view(req, resp):
        resp.text = "hello"
        resp.headers["content-type"] = "application/x-custom"

    r = api.requests.get("/")
    assert r.headers.get_list("content-type") == ["application/x-custom"]


def test_mixed_case_header_set_twice_sends_one_value(api):
    @api.route("/")
    def view(req, resp):
        resp.text = "hi"
        resp.headers["X-Thing"] = "one"
        resp.headers["x-thing"] = "two"

    r = api.requests.get("/")
    assert r.headers.get_list("x-thing") == ["two"]


def test_no_content_drops_any_content_type_spelling(api):
    @api.route("/")
    def view(req, resp):
        resp.headers["CONTENT-TYPE"] = "text/weird"
        resp.no_content()

    r = api.requests.get("/")
    assert r.status_code == 204
    assert "content-type" not in r.headers


def test_response_headers_reads_are_case_insensitive(api):
    @api.route("/")
    def view(req, resp):
        resp.text = "ok"
        resp.headers["X-Foo"] = "bar"
        assert resp.headers["x-foo"] == "bar"
        assert "X-FOO" in resp.headers
        assert resp.headers.get("x-FOO") == "bar"

    r = api.requests.get("/")
    assert r.headers["x-foo"] == "bar"


class TestCaseInsensitiveDictHardening:
    def test_init_lowercase_lookup(self):
        d = CaseInsensitiveDict({"X-Bar": "2"})
        assert d["x-bar"] == "2"
        assert d.get("X-BAR") == "2"
        assert "x-bar" in d

    def test_init_kwargs(self):
        d = CaseInsensitiveDict(None, Accept="json")
        assert d["accept"] == "json"

    def test_init_iterable_of_pairs(self):
        d = CaseInsensitiveDict([("X-One", "1"), ("X-Two", "2")])
        assert d["x-one"] == "1"
        assert d["x-two"] == "2"

    def test_ior_goes_through_override(self):
        d = CaseInsensitiveDict({"A": "1"})
        d |= {"X-Foo": "v", "a": "2"}
        assert d["x-foo"] == "v"
        assert d["A"] == "2"
        assert len(d) == 2

    def test_copy_returns_case_insensitive_dict(self):
        d = CaseInsensitiveDict({"X-Bar": "2"})
        c = d.copy()
        assert isinstance(c, CaseInsensitiveDict)
        assert c["x-bar"] == "2"
        assert list(c) == ["X-Bar"]  # casing preserved
        c["x-new"] = "3"
        assert "x-new" not in d  # independent copy

    def test_fromkeys(self):
        d = CaseInsensitiveDict.fromkeys(["X-A", "X-B"], "v")
        assert isinstance(d, CaseInsensitiveDict)
        assert d["x-a"] == "v"
        assert d["X-B"] == "v"

    def test_iteration_preserves_original_casing(self):
        d = CaseInsensitiveDict()
        d["Content-Type"] = "text/html"
        d["X-Custom-Header"] = "1"
        assert list(d) == ["Content-Type", "X-Custom-Header"]
        assert dict(d.items()) == {
            "Content-Type": "text/html",
            "X-Custom-Header": "1",
        }

    def test_reset_takes_latest_casing_without_duplicates(self):
        d = CaseInsensitiveDict()
        d["Content-Type"] = "a"
        d["content-type"] = "b"
        assert len(d) == 1
        assert list(d) == ["content-type"]
        assert d["CONTENT-TYPE"] == "b"

    def test_delete_and_pop_any_casing(self):
        d = CaseInsensitiveDict({"X-Bar": "2", "X-Baz": "3"})
        del d["x-bar"]
        assert "X-Bar" not in d
        assert d.pop("X-BAZ") == "3"
        assert d.pop("missing", "fallback") == "fallback"

    def test_setdefault_matches_existing_casing(self):
        d = CaseInsensitiveDict({"X-Bar": "2"})
        assert d.setdefault("x-bar", "other") == "2"
        assert d.setdefault("X-New", "n") == "n"
        assert d["x-new"] == "n"

    def test_clear_resets_lookup_table(self):
        d = CaseInsensitiveDict({"X-Bar": "2"})
        d.clear()
        assert "x-bar" not in d
        d["X-Bar"] = "3"
        assert d["x-bar"] == "3"
        assert len(d) == 1

    def test_popitem_keeps_lookup_in_sync(self):
        d = CaseInsensitiveDict({"X-Bar": "2"})
        key, value = d.popitem()
        assert (key, value) == ("X-Bar", "2")
        assert "x-bar" not in d

    def test_pickle_round_trip(self):
        # pickle used to replay items through __setitem__ before __init__
        # ran, so loads() raised AttributeError on the missing _lower index.
        d = CaseInsensitiveDict({"X-Bar": "2"})
        p = pickle.loads(pickle.dumps(d))  # noqa: S301 — trusted test data
        assert isinstance(p, CaseInsensitiveDict)
        assert list(p) == ["X-Bar"]  # casing preserved
        assert p["x-bar"] == "2"  # case-insensitivity survives
        p["X-New"] = "n"
        assert "x-new" not in d  # no shared state

    def test_copy_copy_does_not_share_state(self):
        # copy.copy used to alias _lower with the original, so mutating
        # the copy corrupted the original ('X-New' in d but d['X-New']
        # raised KeyError).
        d = CaseInsensitiveDict({"X-Bar": "2"})
        c = copy.copy(d)
        assert isinstance(c, CaseInsensitiveDict)
        assert list(c) == ["X-Bar"]
        c["X-New"] = "3"
        assert "x-new" not in d
        assert c["x-new"] == "3"
        assert d["x-bar"] == "2" and c["x-bar"] == "2"
        del c["X-BAR"]
        assert d["x-bar"] == "2"  # original untouched

    def test_deepcopy_round_trip(self):
        d = CaseInsensitiveDict({"X-List": ["a"]})
        dc = copy.deepcopy(d)
        assert isinstance(dc, CaseInsensitiveDict)
        assert dc["x-list"] == ["a"]
        dc["x-list"].append("b")
        assert d["X-List"] == ["a"]  # values deep-copied
        dc["X-New"] = "n"
        assert "x-new" not in d
