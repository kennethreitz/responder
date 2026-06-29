Migrating to v7
===============

Responder 7.0 focuses on explicit runtime contracts: framework errors use
problem details by default, auth can be declared at the app level, dependencies
can be attached to routes as graph-aware guards, and optional production server
tooling lives behind the ``server`` extra.


Problem Details by Default
--------------------------

Framework-generated errors now use RFC 9457-style
``application/problem+json`` responses by default::

    {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Not Found"
    }

This applies to framework errors such as 404, 405, request parsing failures,
validation failures, request timeouts, auth failures, and production
response-model validation failures. When validation details exist, the framework
still includes them as the extension member ``errors``:

    {
        "type": "about:blank",
        "title": "Validation Error",
        "status": 422,
        "detail": "Validation failed",
        "errors": [{...}],
    }

If you need the previous content-negotiated behavior while migrating, pass
``problem_details=False``::

    api = responder.API(problem_details=False)


App-Level Auth
--------------

Routes can still declare auth directly::

    @api.get("/me", auth=bearer)
    def me(req, resp, *, user):
        resp.media = {"user": user}

In v7, apps can also define a default auth scheme. Routes inherit it unless
they explicitly opt out with ``auth=None``::

    api = responder.API(auth=bearer)

    @api.post("/login", auth=None)
    def login(req, resp):
        resp.media = {"token": issue_token()}

    @api.get("/me")
    def me(req, resp, *, user):
        resp.media = {"user": user}

When OpenAPI is enabled, inherited auth is documented on protected operations
and ``auth=None`` marks public operations with an empty security requirement.


Route Dependency Guards
-----------------------

``Depends(...)`` still injects local dependency values into handler parameters.
Use route-level ``dependencies=[Depends(...)]`` when the dependency is a guard
or setup step and the handler does not need its return value. This keeps the
call in the dependency graph with caching/teardown behavior, and still works
for side effects::

    def require_user(req):
        if "Authorization" not in req.headers:
            responder.abort(401, detail="Not authenticated")

    @api.get("/private", dependencies=[Depends(require_user)])
    def private(req, resp):
        resp.media = {"ok": True}

Route dependencies follow the same lifecycle rules as parameter dependencies,
including sync/async providers, sub-dependencies, and generator teardown.

For raw before/after hooks, use ``before=``/``after=`` to keep intent explicit.
Route execution order is now explicit: global ``before_request`` hooks, route
``before`` hooks, auth, validation, route ``dependencies=...``, handler,
response-model checks, and finally ``after`` hooks.

This ordering matters if a route has both hooks and dependency-guards.


Request Model Compatibility Note
-------------------------------

``request_model=`` is deprecated in favor of inline body-parameter validation
and now emits ``DeprecationWarning`` during registration.


Server Extra
------------

The default install still includes the uvicorn runner used by ``api.run()``.
Install ``responder[server]`` when you want the optional Granian server too::

    uv pip install 'responder[server]'

Then run the current app with Granian's embedded ASGI server::

    api.run(server="granian")

If Granian is not installed, ``server="granian"`` raises a runtime error with
the install command. For multi-worker production deployments, point Granian's
CLI at your ASGI app::

    granian --interface asgi --host 0.0.0.0 --port 8000 api:api


Request Method Type
-------------------

``req.method`` now returns an exact ``str``. It is still uppercase
(``"GET"``, ``"POST"``, etc.) and comparisons remain case-sensitive. The old
exported ``HTTPMethod`` subclass has been removed.
