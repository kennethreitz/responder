Migrating to v9
===============

Responder 9.0 makes the v8.1 deprecation path the default behavior. Most
apps only need small cleanup: use the supported test-client helpers, pass
explicit route endpoints, and rely on precision-preserving JSON defaults.

The compatibility switches documented below are explicit and quiet. They are
there for apps that need to keep 8.x behavior while migrating.

Run your test suite with warnings surfaced to find any unrelated deprecations::

    python -W error::DeprecationWarning -m pytest

Multipart uploads stream to disk
--------------------------------

Multipart bodies now parse incrementally as they arrive: file parts spool to
temporary files (rolling to disk past ~1 MB) instead of being buffered in
RAM. Nothing changes in how you read uploads — ``File()`` markers,
``req.media("files")``, and ``req.media("form")`` all work as before, and
``UploadFile.save()`` copies disk-to-disk in chunks.

Two consequences of the body no longer being buffered:

* **The raw body is consumed by the parse.** ``await req.content`` after a
  multipart parse raises a ``RuntimeError`` (previously it returned the full
  buffered body). If a handler needs both the raw bytes and the parsed form,
  await ``req.content`` *first* — parsing then falls back to the buffered
  body, exactly like 8.x. Handlers using ``File()``/``Form()`` markers have
  the form parsed before they run, so the raw multipart body is not available
  to them at all (this matches Starlette and FastAPI).

* **Text fields share the streaming parser's limits.** ``req.media("form")``
  on a multipart body now goes through the same parse as ``media("files")``:
  at most 1000 parts and 1 MB per text field (oversized or malformed bodies
  get a ``400``). Text fields with invalid UTF-8 are now decoded lossily
  instead of being silently dropped.

Request bodies are capped at 100 MiB by default
-----------------------------------------------

``max_request_size`` now defaults to 100 MiB instead of unlimited; larger
bodies get a ``413``. The cap is enforced chunk-by-chunk on the wire, and
since multipart uploads spool to disk, raising it does not raise memory
use::

    api = responder.API(max_request_size=5 * 1024**3)  # allow 5 GiB uploads
    api = responder.API(max_request_size=None)         # pre-9.0 unlimited

``api.session()`` was removed
-----------------------------

The legacy test-client accessor ``api.session()`` has been removed. Use the
:attr:`~responder.API.requests` property instead::

    r = api.requests.get("http://;/hello")

If you relied on ``session(base_url=...)`` for a custom base URL, construct
the client through the supported helper::

    client = api.test_client(base_url="http://testserver")

Explicit ``port=`` now wins over ``PORT``
-----------------------------------------

When ``api.run()`` / ``api.serve()`` receive both an explicit ``port=`` and
a conflicting ``PORT`` environment variable, the explicit argument wins::

    # PORT=9000 in the environment:
    api.run(port=8000)   # binds 8000
    api.run(port=9000)   # binds 9000
    api.run()            # binds 9000

To keep environment-first behavior, resolve the environment yourself::

    api.run(port=int(os.environ.get("PORT", 8000)))

or use the legacy compatibility switch while migrating::

    api.run(port=8000, port_precedence="env")

Bare ``add_route()`` static fallback
------------------------------------

Calling ``api.add_route(route)`` with no endpoint now raises by default
instead of implicitly registering a *default* route that serves
``static/index.html`` for every unmatched request. Pass an endpoint
explicitly instead::

    import pathlib

    async def spa(req, resp):
        resp.html = (pathlib.Path("static") / "index.html").read_text()

    api.add_route("/", spa, default=True)

Static *assets* are unaffected: the ``static_dir`` / ``static_route`` mount
keeps working as-is.

If you need the old fallback while migrating, opt in explicitly::

    api = responder.API(implicit_static_fallback=True)

``Decimal`` serializes as a JSON string
---------------------------------------

Assigning a bare :class:`decimal.Decimal` to ``resp.media`` now serializes it
as a JSON string, preserving precision.

Choose a representation explicitly when your API contract needs a number::

    resp.media = {"price": str(total)}    # exact, v9's default
    resp.media = {"price": float(total)}  # lossy, JSON number

or keep floats everywhere with the compatibility flag or a custom encoder::

    api = responder.API(json_decimal="float")

    def encoder(obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        raise TypeError  # fall back to the built-in conversions

    api = responder.API(encoder=encoder)

GraphQL: ``400`` with partial data
-----------------------------------

The GraphQL extension now returns HTTP ``200`` for execution results that
contain both ``data`` and ``errors`` (a *partial* result, e.g. one resolver
failed while others succeeded). Per the `GraphQL-over-HTTP specification
<https://graphql.github.io/graphql-over-http/>`_, these are well-formed
GraphQL responses.

Requests that produce *no* data (validation or request errors) keep their
``400``. Inspect the ``errors`` key of the response body instead of relying
on the status code alone::

    api.graphql("/graph", schema=schema)

    result = client.post("/graph", json={"query": query}).json()
    if result.get("errors"):
        ...  # handle errors, regardless of HTTP status

To preserve the legacy partial-data status while migrating, pass::

    api.graphql("/graph", schema=schema, partial_data_status=400)
