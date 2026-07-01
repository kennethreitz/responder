Composing Apps with Routers
===========================

As an application grows past a single file, you'll want to declare routes
where the code lives — a ``users`` module owns the user routes, a
``billing`` module owns billing — and assemble everything in one place.
If you've used Flask Blueprints or FastAPI's ``APIRouter``, this is the
same idea: :class:`responder.Router` records route declarations *without*
an ``API`` instance, and ``api.include_router()`` attaches them later.

This avoids the two classic failure modes of single-``API`` apps: stuffing
every route into one file, or importing the ``api`` object into every
module (and the circular imports that follow).


Declaring Routes in a Module
----------------------------

A ``Router`` supports the same decorators as the ``API`` — ``route()``,
the verb shortcuts (``get``, ``post``, ``put``, ``patch``, ``delete``),
``websocket_route()``, and ``before_request`` — but only records the
declarations::

    # users.py
    from responder import Router

    router = Router(prefix="/users", tags=["users"])

    @router.get("")
    def list_users(req, resp):
        resp.media = []

    @router.get("/{user_id:int}")
    def get_user(req, resp, *, user_id):
        resp.media = {"id": user_id}

Nothing runs yet — there is no application here to run. The main module
assembles the app::

    # app.py
    import responder

    import billing
    import users

    api = responder.API()
    api.include_router(users.router, prefix="/v1")    # /v1/users, /v1/users/{id}
    api.include_router(billing.router, prefix="/v1")

Each recorded route is replayed through ``api.route()``, so everything
works exactly as if it had been declared on the API directly: auth
inheritance, ``Depends`` guards, Pydantic models, and OpenAPI metadata.

Inclusion is a *snapshot*: routes declared on a router after it has been
included are not picked up by that earlier inclusion.


Nesting Routers
---------------

Routers include other routers, and prefixes compose::

    api_v1 = Router(prefix="/v1")
    api_v1.include_router(users.router)     # /v1/users/...
    api_v1.include_router(admin.router, prefix="/admin")

    api.include_router(api_v1)

Along the way, ``tags`` merge (outermost first, duplicates dropped) and
``dependencies`` concatenate (outermost guards run first).


Group Defaults: Tags, Dependencies, Auth
----------------------------------------

Group-level values apply to every route in the router — and can still be
overridden per route::

    from responder import Depends, Router
    from responder.ext.auth import BearerAuth

    def require_staff(req):
        ...

    admin = Router(
        prefix="/admin",
        tags=["admin"],
        dependencies=[Depends(require_staff)],
        auth=BearerAuth(verify=lookup_token),
    )

    @admin.get("/stats")
    def stats(req, resp, *, user):
        resp.media = {"user": user}

    @admin.get("/health", auth=None)     # opt this one route out of auth
    def health(req, resp):
        resp.media = {"ok": True}

Auth resolution picks the most specific explicit setting: a route's own
``auth=`` wins over the router's, which wins over
``include_router(..., auth=...)``, which wins over the app-level
``API(auth=...)`` default.

Before-request hooks declared on a router only run for request paths under
the prefix the router was mounted at (like ``api.group()`` hooks)::

    @admin.before_request()
    def audit(req, resp):
        log.info("admin request", path=req.url.path)


Including a Router Twice
------------------------

The same router can be included at several prefixes — handy for serving an
API under a legacy path during a migration::

    api.include_router(users.router, prefix="/v1")
    api.include_router(users.router, prefix="/api/v1")

One caveat: route metadata (auth, tags, dependencies) is attached to the
view function itself, so including the same *view* twice with different
effective metadata raises a ``ValueError`` rather than silently rewriting
the earlier inclusion. Include with identical settings, or use separate
routers with separate view functions.


Routers vs. ``api.group()``
---------------------------

``api.group(prefix)`` remains for quick, same-file prefix grouping — it
registers routes immediately on the live API. Reach for ``Router`` when
routes live in their own modules, when you need group-level ``tags`` /
``dependencies`` / ``auth``, or when groups need to nest.
