import pytest
from responder import routes


def test_memoize():
    def blah():
        pass


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


@pytest.mark.parametrize(
    "path_param, actual, match",
    [
        pytest.param(
            "/{greetings}", "/hello", {"greetings": "hello"}, id="with one strformat"
        ),
        pytest.param(
            "/{greetings}.{name}",
            "/hi.jane",
            {"greetings": "hi", "name": "jane"},
            id="with dot in url and two strformat",
        ),
        pytest.param(
            "/{greetings}/{name}",
            "/hi/john",
            {"greetings": "hi", "name": "john"},
            id="with sub url and two strformat",
        ),
        pytest.param(
            "/concrete_path", "/foo", {}, id="test concrete path with no match"
        ),
    ],
)
def test_incoming_matches(path_param, actual, match):
    r = routes.Route(path_param, "test_endpoint")
    assert r.incoming_matches(actual) == match


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
