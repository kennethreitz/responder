import pytest
from responder import routes


@pytest.mark.parametrize(
    "route, expected",
    [
        pytest.param("/", False, id="home path without params"),
        pytest.param("/test_path", False, id="sub path without params"),
        pytest.param("/{test_path}", True, id="path with params"),
    ],
)
def test_parameter(route, expected):
    r = routes.Route(route, "test_endpoint")
    assert r.has_parameters is expected


def test_url():
    r = routes.Route("/{my_path}", "test_endpoint")
    url = r.url(my_path="path")
    assert url == "/path"


def test_equal():
    r = routes.Route("/{path_param}", "test_endpoint")
    r2 = routes.Route("/{path_param}", "test_endpoint")
    r3 = routes.Route("/test_path", "test_endpoint")

    assert r == r2
    assert r != r3


def test_incoming_matches():
    # Test Route with one param
    r = routes.Route("/{greetings}", "test_endpoint")
    assert r.incoming_matches("/hello") == {"greetings": "hello"}
    assert r.incoming_matches("/foo") == {"greetings": "foo"}

    assert r._memo == {
        "incoming_matches:/hello": {"greetings": "hello"},
        "incoming_matches:/foo": {"greetings": "foo"},
    }

    # Test Route with two params
    r = routes.Route("/{greetings}/{name}", "test_endpoint")
    assert r.incoming_matches("/hi/john") == {"greetings": "hi", "name": "john"}
    assert r.incoming_matches("/hello/jane") == {"greetings": "hello", "name": "jane"}

    # Test Route with no param
    assert r._memo == {
        "incoming_matches:/hi/john": {"greetings": "hi", "name": "john"},
        "incoming_matches:/hello/jane": {"greetings": "hello", "name": "jane"},
    }

    r = routes.Route("/hello", "test_endpoint")
    assert r.incoming_matches("/hello") == {}
    assert r.incoming_matches("/bye") == {}
    assert r._memo == {"incoming_matches:/hello": {}, "incoming_matches:/bye": {}}


def test_incoming_matches_with_concrete_path_no_match():
    r = routes.Route("/concrete_path", "test_endpoint")
    assert r.incoming_matches("hello") == {}


@pytest.mark.parametrize(
    "route, match, expected",
    [
        pytest.param(
            "/{path_param}",
            "/{path_param}",
            True,
            id="with both parametrized path match",
        ),
        pytest.param(
            "/concrete", "/concrete", True, id="with both concrete path match"
        ),
        pytest.param("/concrete", "/no_match", False, id="with no match"),
    ],
)
def test_does_match_with_route(route, match, expected):
    r = routes.Route(route, "test_endpoint")
    assert r.does_match(match) == expected


@pytest.mark.parametrize(
    "path_param, expected_weight",
    [
        pytest.param("/{greetings}", (True, -1), id="with one param"),
        pytest.param(
            "/{greetings}.{name}", (True, -2), id="with 2 params and dot in the middle"
        ),
        pytest.param("/{greetings}/{name}", (True, -2), id="with 2 param and subpath"),
        pytest.param(
            "/{greetings}/{name}/{hello}", (True, -3), id="with 3 param and subpath"
        ),
        pytest.param(
            "/{greetings}_{name}", (True, -2), id="with 2 param and underscore"
        ),
        pytest.param("/hello", (False, 0), id="with 2 param and underscore"),
    ],
)
def test_weight(path_param, expected_weight):
    r = routes.Route(path_param, "test_endpoint")
    assert r._weight() == expected_weight
