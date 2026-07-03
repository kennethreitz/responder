"""Tests for the 8.1.0 models features:

- req.headers.get_list() — multi-value request header access (item 36)
- If-Match / If-Unmodified-Since / write-side If-None-Match -> 412 (item 34)
- q-ranked Accept preference order in content negotiation, and
  req.preferred_media_type() (item 30)
"""

import copy
import json
import pickle
from datetime import UTC, datetime

import msgpack
import pytest
from starlette.testclient import TestClient

import responder
from responder.models import CaseInsensitiveDict


def _api():
    return responder.API(allowed_hosts=[";"], session_https_only=False)


def _client(api):
    return TestClient(api, base_url="http://;")


# ---------------------------------------------------------------------------
# CaseInsensitiveDict.get_list (unit)
# ---------------------------------------------------------------------------


def test_get_list_single_value():
    d = CaseInsensitiveDict({"Content-Type": "text/plain"})
    assert d.get_list("content-type") == ["text/plain"]


def test_get_list_missing_key_returns_empty_list():
    d = CaseInsensitiveDict()
    assert d.get_list("X-Nope") == []


def test_get_list_missing_key_returns_default():
    d = CaseInsensitiveDict()
    assert d.get_list("X-Nope", ["fallback"]) == ["fallback"]


def test_setitem_resets_multi_values():
    d = CaseInsensitiveDict()
    d._add("Via", "a")
    d._add("Via", "b")
    assert d.get_list("via") == ["a", "b"]
    d["Via"] = "c"
    assert d.get_list("via") == ["c"]
    assert d["via"] == "c"


def test_delitem_clears_multi_values():
    d = CaseInsensitiveDict()
    d._add("Via", "a")
    d._add("via", "b")
    del d["VIA"]
    assert d.get_list("via") == []
    assert "via" not in d


def test_pop_clears_multi_values():
    d = CaseInsensitiveDict()
    d._add("Via", "a")
    d._add("Via", "b")
    assert d.pop("via") == "b"
    assert d.get_list("Via") == []
    assert d.pop("via", "gone") == "gone"


def test_clear_clears_multi_values():
    d = CaseInsensitiveDict()
    d._add("Via", "a")
    d._add("Via", "b")
    d.clear()
    assert d.get_list("via") == []


def test_copy_preserves_multi_values():
    d = CaseInsensitiveDict()
    d._add("X-Forwarded-For", "1.1.1.1")
    d._add("X-Forwarded-For", "2.2.2.2")
    copied = d.copy()
    assert copied.get_list("x-forwarded-for") == ["1.1.1.1", "2.2.2.2"]
    # Mutating the copy leaves the original alone.
    copied["X-Forwarded-For"] = "3.3.3.3"
    assert d.get_list("x-forwarded-for") == ["1.1.1.1", "2.2.2.2"]


def test_get_list_returns_a_copy():
    d = CaseInsensitiveDict()
    d._add("Via", "a")
    d.get_list("via").append("tampered")
    assert d.get_list("via") == ["a"]


# --- FINDING 7: copy.copy / deepcopy / pickle must preserve _multi -----------


def _multi_dict():
    d = CaseInsensitiveDict()
    d._add("X-Forwarded-For", "1.1.1.1")
    d._add("X-Forwarded-For", "2.2.2.2")
    return d


def test_copy_copy_preserves_multi_values():
    copied = copy.copy(_multi_dict())
    assert copied.get_list("x-forwarded-for") == ["1.1.1.1", "2.2.2.2"]


def test_deepcopy_preserves_multi_values():
    copied = copy.deepcopy(_multi_dict())
    assert copied.get_list("x-forwarded-for") == ["1.1.1.1", "2.2.2.2"]


def test_pickle_preserves_multi_values():
    restored = pickle.loads(pickle.dumps(_multi_dict()))  # noqa: S301 — trusted test data
    assert restored.get_list("x-forwarded-for") == ["1.1.1.1", "2.2.2.2"]
    # The single-value view and case-insensitive index still work.
    assert restored["x-forwarded-for"] == "2.2.2.2"


def test_copy_round_trip_is_independent():
    original = _multi_dict()
    copied = copy.deepcopy(original)
    copied._add("X-Forwarded-For", "3.3.3.3")
    # Mutating the copy leaves the original's multi-store untouched.
    assert original.get_list("x-forwarded-for") == ["1.1.1.1", "2.2.2.2"]


def test_add_keeps_last_value_for_single_access():
    d = CaseInsensitiveDict()
    d._add("X-Thing", "first")
    d._add("x-thing", "second")
    assert d["X-THING"] == "second"
    assert d.get("x-thing") == "second"
    assert len(d) == 1


# ---------------------------------------------------------------------------
# req.headers.get_list end-to-end
# ---------------------------------------------------------------------------


def test_request_headers_get_list_preserves_duplicate_lines():
    api = _api()

    @api.route("/h")
    def h(req, resp):
        resp.media = {
            "one": req.headers["x-forwarded-for"],
            "all": req.headers.get_list("X-Forwarded-For"),
        }

    r = _client(api).get(
        "/h",
        headers=[
            ("X-Forwarded-For", "1.1.1.1"),
            ("X-Forwarded-For", "2.2.2.2"),
            ("X-Forwarded-For", "3.3.3.3"),
        ],
    )
    assert r.json() == {
        "one": "3.3.3.3",  # single-value access: last line wins (unchanged)
        "all": ["1.1.1.1", "2.2.2.2", "3.3.3.3"],
    }


def test_request_headers_get_list_single_and_missing():
    api = _api()

    @api.route("/h")
    def h(req, resp):
        resp.media = {
            "ua": req.headers.get_list("user-agent"),
            "missing": req.headers.get_list("X-Missing"),
        }

    r = _client(api).get("/h", headers={"User-Agent": "tester"})
    assert r.json() == {"ua": ["tester"], "missing": []}


# ---------------------------------------------------------------------------
# If-Match / If-Unmodified-Since / write-side If-None-Match -> 412
# ---------------------------------------------------------------------------


def _etag_api(etag="v1"):
    api = _api()

    @api.route("/item", methods=["GET", "POST", "PUT", "DELETE"])
    def item(req, resp):
        resp.etag = etag
        resp.media = {"ok": True}

    return api


def test_if_match_mismatch_is_412():
    r = _client(_etag_api()).put("/item", headers={"If-Match": '"stale"'})
    assert r.status_code == 412
    # The 412 carries the current validator so the client can re-sync.
    assert r.headers["ETag"] == '"v1"'
    assert r.content == b""


def test_if_match_match_succeeds():
    r = _client(_etag_api()).put("/item", headers={"If-Match": '"v1"'})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_if_match_list_with_match_succeeds():
    r = _client(_etag_api()).put(
        "/item", headers={"If-Match": '"other", "v1", "more"'}
    )
    assert r.status_code == 200


def test_if_match_star_succeeds_when_etag_set():
    r = _client(_etag_api()).put("/item", headers={"If-Match": "*"})
    assert r.status_code == 200


def test_if_match_requires_strong_comparison():
    # A weak current ETag can never strong-match If-Match (RFC 9110 §13.1.1).
    r = _client(_etag_api(etag='W/"v1"')).put(
        "/item", headers={"If-Match": 'W/"v1"'}
    )
    assert r.status_code == 412


def test_if_match_delete_mismatch_is_412():
    r = _client(_etag_api()).delete("/item", headers={"If-Match": '"stale"'})
    assert r.status_code == 412


def test_if_match_ignored_without_etag():
    api = _api()

    @api.route("/plain", methods=["PUT"])
    def plain(req, resp):
        resp.media = {"ok": True}

    r = _client(api).put("/plain", headers={"If-Match": '"anything"'})
    assert r.status_code == 200


def test_if_match_ignored_on_get():
    # Read-side conditionals are unchanged: If-Match doesn't 412 a GET.
    r = _client(_etag_api()).get("/item", headers={"If-Match": '"stale"'})
    assert r.status_code == 200


def test_if_none_match_still_304_on_get():
    r = _client(_etag_api()).get("/item", headers={"If-None-Match": '"v1"'})
    assert r.status_code == 304


def test_if_none_match_match_on_write_is_412():
    client = _client(_etag_api())
    assert client.post("/item", headers={"If-None-Match": '"v1"'}).status_code == 412
    # Weak comparison applies to If-None-Match.
    assert (
        client.post("/item", headers={"If-None-Match": 'W/"v1"'}).status_code == 412
    )


def test_if_none_match_star_on_write_is_412():
    r = _client(_etag_api()).put("/item", headers={"If-None-Match": "*"})
    assert r.status_code == 412


def test_if_none_match_no_match_on_write_succeeds():
    r = _client(_etag_api()).put("/item", headers={"If-None-Match": '"other"'})
    assert r.status_code == 200


def _dated_api():
    api = _api()

    @api.route("/dated", methods=["GET", "PUT"])
    def dated(req, resp):
        resp.last_modified = datetime(2026, 1, 15, tzinfo=UTC)
        resp.media = {"ok": True}

    return api


def test_if_unmodified_since_older_is_412():
    r = _client(_dated_api()).put(
        "/dated", headers={"If-Unmodified-Since": "Wed, 14 Jan 2026 00:00:00 GMT"}
    )
    assert r.status_code == 412
    assert r.headers["Last-Modified"] == "Thu, 15 Jan 2026 00:00:00 GMT"


def test_if_unmodified_since_current_or_newer_succeeds():
    client = _client(_dated_api())
    same = client.put(
        "/dated", headers={"If-Unmodified-Since": "Thu, 15 Jan 2026 00:00:00 GMT"}
    )
    newer = client.put(
        "/dated", headers={"If-Unmodified-Since": "Fri, 16 Jan 2026 00:00:00 GMT"}
    )
    assert same.status_code == 200
    assert newer.status_code == 200


def test_if_unmodified_since_invalid_date_is_ignored():
    r = _client(_dated_api()).put(
        "/dated", headers={"If-Unmodified-Since": "not a date"}
    )
    assert r.status_code == 200


def test_if_unmodified_since_ignored_when_if_match_present():
    # RFC 9110 §13.2.2: If-Unmodified-Since is evaluated only when If-Match
    # is absent — a passing If-Match wins over a stale date.
    api = _api()

    @api.route("/both", methods=["PUT"])
    def both(req, resp):
        resp.etag = "v1"
        resp.last_modified = datetime(2026, 1, 15, tzinfo=UTC)
        resp.media = {"ok": True}

    r = _client(api).put(
        "/both",
        headers={
            "If-Match": '"v1"',
            "If-Unmodified-Since": "Wed, 14 Jan 2026 00:00:00 GMT",
        },
    )
    assert r.status_code == 200


def test_precondition_skipped_on_non_2xx():
    # A handler that already failed keeps its status (RFC 9110 §13.2.1).
    api = _api()

    @api.route("/missing", methods=["PUT"])
    def missing(req, resp):
        resp.etag = "v1"
        resp.status_code = 404
        resp.media = {"error": "not found"}

    r = _client(api).put("/missing", headers={"If-Match": '"stale"'})
    assert r.status_code == 404


def test_precondition_applies_to_201():
    api = _api()

    @api.route("/created", methods=["PUT"])
    def created(req, resp):
        resp.etag = "v1"
        resp.status_code = 201
        resp.media = {"ok": True}

    client = _client(api)
    assert client.put("/created", headers={"If-Match": '"stale"'}).status_code == 412
    assert client.put("/created", headers={"If-Match": '"v1"'}).status_code == 201


def test_unconditional_write_unaffected():
    r = _client(_etag_api()).put("/item")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# --- FINDING 5: repeated precondition header lines are folded (RFC 9110) ------


def test_if_match_repeated_lines_with_match_succeeds():
    # Two If-Match lines must be folded into one list; a matching member
    # ("v1") permits the write instead of spuriously 412-ing on the last line.
    r = _client(_etag_api()).put(
        "/item",
        headers=[("If-Match", '"v1"'), ("If-Match", '"other"')],
    )
    assert r.status_code == 200


def test_if_match_repeated_lines_all_mismatch_is_412():
    r = _client(_etag_api()).put(
        "/item",
        headers=[("If-Match", '"stale"'), ("If-Match", '"other"')],
    )
    assert r.status_code == 412


def test_if_none_match_repeated_lines_match_on_write_is_412():
    # Both lines folded: "v1" matches, so the lost-update guard fires (412)
    # instead of failing open because only the last line ("other") was seen.
    r = _client(_etag_api()).put(
        "/item",
        headers=[("If-None-Match", '"v1"'), ("If-None-Match", '"other"')],
    )
    assert r.status_code == 412


def test_if_none_match_repeated_lines_no_match_on_write_succeeds():
    r = _client(_etag_api()).put(
        "/item",
        headers=[("If-None-Match", '"a"'), ("If-None-Match", '"b"')],
    )
    assert r.status_code == 200


def test_if_none_match_repeated_lines_still_304_on_get():
    # The read-side conditional folds repeated lines too.
    r = _client(_etag_api()).get(
        "/item",
        headers=[("If-None-Match", '"other"'), ("If-None-Match", '"v1"')],
    )
    assert r.status_code == 304


# ---------------------------------------------------------------------------
# Content negotiation: q-ranked Accept preference order
# ---------------------------------------------------------------------------


def _media_api():
    api = _api()

    @api.route("/data")
    def data(req, resp):
        resp.media = {"key": "value"}

    return api


def test_negotiation_prefers_higher_q_format():
    # Previously JSON won here purely by registration order.
    r = _client(_media_api()).get(
        "/data",
        headers={"Accept": "application/yaml;q=0.9, application/json;q=0.1"},
    )
    assert r.headers["Content-Type"].startswith("application/yaml")
    assert "key: value" in r.text


def test_negotiation_prefers_json_when_it_ranks_higher():
    r = _client(_media_api()).get(
        "/data",
        headers={"Accept": "application/yaml;q=0.1, application/json;q=0.9"},
    )
    assert r.headers["Content-Type"].startswith("application/json")
    assert r.json() == {"key": "value"}


def test_negotiation_msgpack_preferred():
    r = _client(_media_api()).get(
        "/data",
        headers={"Accept": "application/x-msgpack, application/json;q=0.5"},
    )
    assert r.headers["Content-Type"] == "application/x-msgpack"
    assert msgpack.unpackb(r.content) == {"key": "value"}


def test_negotiation_default_is_json_without_accept():
    # An explicit empty Accept exercises the "absent header" branch (the
    # TestClient otherwise sends */*).
    r = _client(_media_api()).get("/data", headers={"Accept": ""})
    assert r.headers["Content-Type"].startswith("application/json")
    assert r.json() == {"key": "value"}


def test_negotiation_wildcard_tie_keeps_json_default():
    r = _client(_media_api()).get("/data", headers={"Accept": "*/*"})
    assert r.headers["Content-Type"].startswith("application/json")


def test_negotiation_q_zero_excludes_json():
    r = _client(_media_api()).get(
        "/data",
        headers={"Accept": "application/json;q=0, application/yaml"},
    )
    assert r.headers["Content-Type"].startswith("application/yaml")


def test_negotiation_unsupported_accept_falls_back_to_json():
    r = _client(_media_api()).get("/data", headers={"Accept": "text/html"})
    assert r.headers["Content-Type"].startswith("application/json")
    assert r.json() == {"key": "value"}


# ---------------------------------------------------------------------------
# Request.preferred_media_type
# ---------------------------------------------------------------------------


def _pref_api(candidates):
    api = _api()

    @api.route("/p")
    def p(req, resp):
        # Emit JSON by hand so the assertion is independent of the response's
        # own content negotiation (some tests send Accept headers that would
        # otherwise negotiate a YAML body).
        resp.text = json.dumps({"preferred": req.preferred_media_type(candidates)})

    return api


def test_preferred_media_type_ranks_by_q():
    api = _pref_api(["application/json", "text/csv"])
    r = _client(api).get(
        "/p", headers={"Accept": "text/csv, application/json;q=0.4"}
    )
    assert r.json() == {"preferred": "text/csv"}


def test_preferred_media_type_tie_keeps_candidate_order():
    api = _pref_api(["application/json", "text/csv"])
    r = _client(api).get("/p", headers={"Accept": "*/*"})
    assert r.json() == {"preferred": "application/json"}


def test_preferred_media_type_no_accept_header_picks_first():
    api = _pref_api(["application/json", "text/csv"])
    r = _client(api).get("/p", headers={"Accept": ""})
    assert r.json() == {"preferred": "application/json"}


def test_preferred_media_type_none_acceptable():
    api = _pref_api(["application/json", "text/csv"])
    r = _client(api).get("/p", headers={"Accept": "image/png"})
    assert r.json() == {"preferred": None}


def test_preferred_media_type_respects_specific_q_zero():
    api = _pref_api(["application/json", "text/csv"])
    r = _client(api).get(
        "/p", headers={"Accept": "application/json;q=0, */*"}
    )
    assert r.json() == {"preferred": "text/csv"}


def test_preferred_media_type_bare_tokens():
    api = _pref_api(["json", "yaml"])
    r = _client(api).get(
        "/p", headers={"Accept": "application/yaml, application/json;q=0.2"}
    )
    assert r.json() == {"preferred": "yaml"}


def test_accepts_unchanged_and_cached_parse():
    # accepts() keeps its public contract after the caching refactor, and
    # repeated calls reuse the parsed header.
    api = _api()

    @api.route("/a")
    def a(req, resp):
        first = req.accepts("application/json")
        second = req.accepts("application/json")
        resp.media = {"first": first, "second": second}

    r = _client(api).get("/a", headers={"Accept": "application/json;q=0.5"})
    assert r.json() == {"first": True, "second": True}
    r = _client(api).get("/a", headers={"Accept": "text/html"})
    assert r.json() == {"first": False, "second": False}


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        ("application/json", 1.0),
        ("application/json;q=0.25", 0.25),
        ("text/html", 0.0),
        ("application/*;q=0.5", 0.5),
    ],
)
def test_accept_quality_values(accept, expected):
    api = _api()
    seen = {}

    @api.route("/q")
    def q(req, resp):
        seen["q"] = req._accept_quality("application/json")
        resp.media = {}

    _client(api).get("/q", headers={"Accept": accept})
    assert seen["q"] == expected
