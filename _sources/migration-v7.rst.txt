Migrating to v7
===============

Responder 7.0 focuses on explicit runtime contracts: framework errors use
problem details by default, auth can be declared at the app level, dependencies
can be attached to routes as guards, and optional production server tooling
lives behind the ``server`` extra.


Problem Details by Default
--------------------------

Framework-generated errors now use RFC 7807-style
``application/problem+json`` responses by default::

    {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Not Found"
    }

This applies to framework errors such as 404, 405, request parsing failures,
validation failures, request timeouts, auth failures, and production
response-model validation failures.

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
or setup step and the handler does not need its return value::

    def require_user(req):
        if "Authorization" not in req.headers:
            responder.abort(401, detail="Not authenticated")

    @api.get("/private", dependencies=[Depends(require_user)])
    def private(req, resp):
        resp.media = {"ok": True}

Route dependencies follow the same lifecycle rules as parameter dependencies,
including sync/async providers, sub-dependencies, and generator teardown.


Server Extra
------------

The default install still includes the uvicorn runner used by ``api.run()``.
Install ``responder[server]`` when you want the optional Granian production
server too::

    uv pip install 'responder[server]'

Then point Granian at your ASGI app::

    granian --interface asgi --host 0.0.0.0 --port 8000 api:api


Request Method Type
-------------------

``req.method`` now returns an exact ``str``. It is still uppercase
(``"GET"``, ``"POST"``, etc.) and comparisons remain case-sensitive. The old
exported ``HTTPMethod`` subclass has been removed.
