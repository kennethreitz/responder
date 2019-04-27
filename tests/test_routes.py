import pytest
from responder import routes


def setup_function(function):
    routes.Route.incoming_matches.cache_clear()


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

    # Test Route with two params
    r = routes.Route("/{greetings}/{name}", "test_endpoint")
    assert r.incoming_matches("/hi/john") == {"greetings": "hi", "name": "john"}
    assert r.incoming_matches("/hello/jane") == {"greetings": "hello", "name": "jane"}

    # Test Route with no param
    r = routes.Route("/hello", "test_endpoint")
    assert r.incoming_matches("/hello") == {}
    assert r.incoming_matches("/bye") == {}


def test_incoming_matches_cache():
    r = routes.Route("/hello", "test_endpoint")
    r.incoming_matches("/hello")
    assert r.incoming_matches.cache_info().hits == 0
    r.incoming_matches("/hello")
    assert r.incoming_matches.cache_info().hits == 1


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
        pytest.param("/{greetings}", (True, True, -1), id="with one param"),
        pytest.param(
            "/{greetings}.{name}",
            (True, True, -2),
            id="with 2 params and dot in the middle",
        ),
        pytest.param(
            "/{greetings}/{name}", (True, True, -2), id="with 2 params and subpath"
        ),
        pytest.param(
            "/{greetings}/{name}/{hello}",
            (True, True, -3),
            id="with 3 params and subpath",
        ),
        pytest.param(
            "/{greetings}_{name}", (True, True, -2), id="with 2 params and underscore"
        ),
        pytest.param("/{greetings}/test", (True, False, -1), id="with one param"),
        pytest.param(
            "/{greetings}.{name}/test",
            (True, False, -2),
            id="with 2 params and dot in the middle",
        ),
        pytest.param(
            "/{greetings}/{name}/test",
            (True, False, -2),
            id="with 2 params and subpath",
        ),
        pytest.param(
            "/{greetings}/{name}/{hello}/test",
            (True, False, -3),
            id="with 3 params and subpath",
        ),
        pytest.param(
            "/{greetings}_{name}/test",
            (True, False, -2),
            id="with 2 params and underscore",
        ),
        pytest.param("/hello", (False, False, 0), id="without params"),
    ],
)
def test_weight(path_param, expected_weight):
    r = routes.Route(path_param, "test_endpoint")
    assert r._weight() == expected_weight


@pytest.mark.parametrize(
    "route, path, expected_result",
    [
        pytest.param("/{greetings:str}", "/hello", {"greetings": "hello"}),
        pytest.param(
            "/{greetings:str}/{who}",
            "/hello/Laidia",
            {"greetings": "hello", "who": "Laidia"},
        ),
        pytest.param("/{birth_date:int}", "/1937", {"birth_date": 1937}),
        pytest.param(
            "/{name:str}/{age:int}", "/Fatna/80", {"name": "Fatna", "age": 80}
        ),
        pytest.param(
            "/{x:float}/{y:float}", "/10.20/75", {"x": float(10.20), "y": float(75)}
        ),
        pytest.param("/{name:str}/{age:int}", "/Fatna/eighty", {}),
        pytest.param("/{greetings:int}", "/hello", {}),
        pytest.param("/{name:float}", "/Fatna", {}),
    ],
)
def test_custom_specifiers(route, path, expected_result):
    r = routes.Route(route, "test_endpoint")
    assert r.incoming_matches(path) == expected_result
