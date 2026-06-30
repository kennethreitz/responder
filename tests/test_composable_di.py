"""v5: composable dependency injection (sub-dependencies, scopes, cycles)."""

import asyncio

import pytest

import responder


@pytest.fixture
def make_api():
    def _make(**kwargs):
        kwargs.setdefault("allowed_hosts", [";"])
        kwargs.setdefault("session_https_only", False)
        return responder.API(**kwargs)

    return _make


def test_subdependency_resolution(make_api):
    api = make_api()

    @api.dependency()
    def config():
        return {"db_url": "sqlite://"}

    @api.dependency()
    def db(config):
        return f"db({config['db_url']})"

    @api.route("/")
    def view(req, resp, *, db):
        resp.text = db

    assert api.requests.get("/").text == "db(sqlite://)"


def test_subdependency_resolved_once_per_request(make_api):
    api = make_api()
    calls = []

    @api.dependency()
    def base():
        calls.append(1)
        return "x"

    @api.dependency()
    def a(base):
        return base

    @api.dependency()
    def b(base):
        return base

    @api.route("/")
    def view(req, resp, *, a, b):
        resp.media = {"a": a, "b": b}

    api.requests.get("/")
    assert len(calls) == 1  # the shared sub-dependency resolves once


def test_teardown_is_reverse_topological(make_api):
    api = make_api()
    order = []

    @api.dependency()
    def base():
        yield "base"
        order.append("base")

    @api.dependency()
    def dependent(base):
        yield base
        order.append("dependent")

    @api.route("/")
    def view(req, resp, *, dependent):
        resp.text = dependent

    api.requests.get("/")
    # The dependent is torn down before its sub-dependency.
    assert order == ["dependent", "base"]


def test_dependency_cycle_is_detected(make_api):
    api = make_api()

    @api.dependency()
    def a(b):
        return b

    @api.dependency()
    def b(a):
        return a

    @api.route("/")
    def view(req, resp, *, a):
        resp.text = str(a)

    with pytest.raises(responder.DependencyCycleError):
        api.requests.get("/")


def test_dependency_resolution_error_reports_clean_chain(make_api):
    api = make_api()

    @api.dependency()
    def db(settings):
        return settings

    @api.route("/")
    def view(req, resp, *, db):
        resp.text = str(db)

    with pytest.raises(responder.DependencyResolutionError) as excinfo:
        api.requests.get("/")

    assert "Dependency chain: db." in str(excinfo.value)
    assert "db -> db" not in str(excinfo.value)


def test_request_injection_by_name_and_type(make_api):
    api = make_api()

    @api.dependency()
    def header_val(req):
        return req.headers.get("X-Test", "none")

    @api.dependency()
    def method_val(ctx: responder.Request):  # injected by type, not name
        return str(ctx.method)

    @api.route("/")
    def view(req, resp, *, header_val, method_val):
        resp.media = {"h": header_val, "m": method_val}

    r = api.requests.get("/", headers={"X-Test": "hi"})
    assert r.json() == {"h": "hi", "m": "GET"}


def test_app_scoped_composition(make_api):
    api = make_api()

    @api.dependency(scope="app")
    def settings():
        return {"name": "app"}

    @api.dependency(scope="app")
    def service(settings):
        return f"svc:{settings['name']}"

    @api.route("/")
    def view(req, resp, *, service):
        resp.text = service

    assert api.requests.get("/").text == "svc:app"
    assert api.requests.get("/").text == "svc:app"  # cached across requests


def test_app_scoped_dependency_concurrent_resolution_runs_once(make_api):
    api = make_api()
    calls = 0

    @api.dependency(scope="app")
    async def settings():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"call": calls}

    async def resolve_many():
        return await asyncio.gather(
            *(
                api.router.app_dependencies.resolve("settings", api.router.dependencies)
                for _ in range(10)
            )
        )

    values = asyncio.run(resolve_many())

    assert calls == 1
    assert values == [{"call": 1}] * 10


def test_app_scoped_dependency_failure_does_not_poison_cache(make_api):
    api = make_api()
    calls = 0

    @api.dependency(scope="app")
    async def settings():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return {"call": calls}

    async def scenario():
        with pytest.raises(RuntimeError, match="temporary"):
            await api.router.app_dependencies.resolve(
                "settings", api.router.dependencies
            )
        second = await api.router.app_dependencies.resolve(
            "settings", api.router.dependencies
        )
        third = await api.router.app_dependencies.resolve(
            "settings", api.router.dependencies
        )
        return second, third

    assert asyncio.run(scenario()) == ({"call": 2}, {"call": 2})


def test_app_scoped_teardown_is_reverse_topological_and_resilient(make_api):
    api = make_api()
    events = []

    @api.dependency(scope="app")
    async def base():
        events.append("open base")
        yield "base"
        events.append("close base")

    @api.dependency(scope="app")
    async def dependent(base):
        events.append(f"open dependent:{base}")
        yield "dependent"
        events.append("close dependent")
        raise RuntimeError("close failed")

    async def resolve_and_shutdown():
        value = await api.router.app_dependencies.resolve(
            "dependent", api.router.dependencies
        )
        await api.router.app_dependencies.shutdown()
        return value

    assert asyncio.run(resolve_and_shutdown()) == "dependent"
    assert events == [
        "open base",
        "open dependent:base",
        "close dependent",
        "close base",
    ]
    assert api.router.app_dependencies.cache == {}


def test_app_scoped_cannot_depend_on_request_scoped(make_api):
    api = make_api()

    @api.dependency()  # request-scoped
    def per_request():
        return "r"

    @api.dependency(scope="app")
    def bad(per_request):
        return per_request

    @api.route("/")
    def view(req, resp, *, bad):
        resp.text = bad

    with pytest.raises(responder.DependencyScopeError):
        api.requests.get("/")


def test_reserved_dependency_name_rejected(make_api):
    api = make_api()

    with pytest.raises(ValueError, match="reserved"):

        @api.dependency(name="req")
        def something():
            return 1


def test_app_scoped_cannot_receive_request(make_api):
    api = make_api()

    with pytest.raises(ValueError, match="cannot receive the request"):

        @api.dependency(scope="app")
        def bad(req):
            return req
