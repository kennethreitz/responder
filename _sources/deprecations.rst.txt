Deprecations (the road to v9)
=============================

Responder 8.1 starts the deprecation clock for a handful of legacy behaviors
that will change or be removed in Responder 9.0. Each emits a
``DeprecationWarning`` (with a migration hint) when the deprecated path runs
— and *only* then; normal usage stays silent. **Behavior is unchanged until
9.0**: the warnings exist so you can migrate on your own schedule.

Run your test suite with warnings surfaced to find any usages::

    python -W error::DeprecationWarning -m pytest

``api.session()`` — use ``api.requests``
----------------------------------------

The legacy test-client accessor ``api.session()`` is deprecated and will be
removed in 9.0. Use the :attr:`~responder.API.requests` property instead::

    # Deprecated
    r = api.session().get("http://;/hello")

    # Preferred
    r = api.requests.get("http://;/hello")

If you relied on ``session(base_url=...)`` for a custom base URL, construct
the client directly::

    from starlette.testclient import TestClient

    client = TestClient(api, base_url="http://testserver")

``PORT`` overriding an explicit ``port=``
-----------------------------------------

Today, ``api.run()`` / ``api.serve()`` let the ``PORT`` environment variable
silently override an explicitly passed ``port=`` argument. Starting with 9.0,
an explicit ``port=`` will take precedence over the environment. A warning is
emitted only when both are set *and disagree*; setting just one (or both to
the same value) stays silent::

    # PORT=9000 in the environment:
    api.run(port=8000)   # DeprecationWarning; binds 9000 today, 8000 in v9
    api.run(port=9000)   # no warning
    api.run()            # no warning; binds 9000

To keep the current behavior explicitly, resolve the environment yourself::

    api.run(port=int(os.environ.get("PORT", 8000)))

Bare ``add_route()`` static fallback
------------------------------------

Calling ``api.add_route(route)`` with no endpoint currently registers an
implicit *default* route that serves ``static/index.html`` for every
unmatched request. This implicit behavior will be removed in 9.0. Pass an
endpoint explicitly instead::

    # Deprecated: implicit static fallback
    api.add_route("/")

    # Preferred: an explicit catch-all view
    async def spa(req, resp):
        resp.html = (pathlib.Path("static") / "index.html").read_text()

    api.add_route("/", spa, default=True)

Static *assets* are unaffected — the ``static_dir`` / ``static_route`` mount
keeps working as-is.

Lossy ``Decimal``-to-float JSON serialization
---------------------------------------------

Assigning a bare :class:`decimal.Decimal` to ``resp.media`` currently
serializes it as a JSON float, silently losing precision. Responder 9.0 will
serialize ``Decimal`` values as strings instead. A warning is emitted once
per process the first time a ``Decimal`` reaches the built-in encoder.

Choose a representation explicitly to be forward-compatible::

    resp.media = {"price": str(total)}    # exact, matches v9's default
    resp.media = {"price": float(total)}  # current behavior, silenced

or keep floats everywhere with a custom encoder (a user ``encoder=`` handles
``Decimal`` before the built-in fallback, so no warning is emitted)::

    def encoder(obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        raise TypeError  # fall back to the built-in conversions

    api = responder.API(encoder=encoder)

GraphQL: ``400`` with partial data
-----------------------------------

The GraphQL extension currently returns HTTP ``400`` whenever the execution
result contains errors — even when ``data`` is non-null (a *partial* result,
e.g. one resolver failed while others succeeded). Per the
`GraphQL-over-HTTP specification
<https://graphql.github.io/graphql-over-http/>`_, such well-formed responses
should be served with ``200``; Responder 9.0 will do so. A warning is emitted
once per process when a partial-data ``400`` is served.

Requests that produce *no* data (validation or request errors) keep their
``400`` in 9.0 as well — only the partial-data case changes. To be
forward-compatible, inspect the ``errors`` key of the response body instead
of relying on the status code::

    result = client.post("/graph", json={"query": query}).json()
    if result.get("errors"):
        ...  # handle errors, regardless of HTTP status
