Migrating to v8
===============

Responder 8.0 focuses on standards alignment and predictable defaults:
redirects preserve the request method, text responses declare their charset the
standard way, YAML uses its registered media type, ``url_for`` fails loudly,
and ``API()`` no longer writes to your filesystem. It also introduces
standalone, composable routers.


New in 8.0: Standalone Routers
------------------------------

``responder.Router`` records route declarations without an ``API`` instance —
like Flask blueprints or FastAPI's ``APIRouter`` — and
``api.include_router(router, prefix=...)`` attaches them::

    # users.py
    from responder import Router

    router = Router(prefix="/users", tags=["users"])

    @router.get("")
    def list_users(req, resp):
        resp.media = {"users": []}

    # app.py
    api = responder.API()
    api.include_router(router, prefix="/v1")

Routers nest, carry group-level ``tags``/``dependencies``/``auth`` defaults,
and scope their ``before_request`` hooks to the mounted prefix. See
:doc:`routers` for the full guide. ``api.group()`` remains for quick same-file
prefix grouping.

The rest of this guide covers the breaking changes.


No Implicit ``static/`` Directory
---------------------------------

Instantiating ``API()`` used to ``mkdir`` an empty ``static/`` into the
working directory (and failed on read-only filesystems). In v8, the default
``static/`` is mounted only when the directory already exists::

    # v7: this created ./static as a side effect
    api = responder.API()

    # v8: create the directory yourself if you want it served
    Path("static").mkdir(exist_ok=True)
    api = responder.API()

An explicitly passed ``static_dir`` that doesn't exist now raises
``FileNotFoundError`` at construction instead of being silently created::

    api = responder.API(static_dir="assets")  # FileNotFoundError if ./assets is missing

``static_dir=None`` still disables static serving entirely. The same applies
conceptually to ``templates_dir``: neither is "created for you" anymore.


``url_for`` Raises ``RouteNotFoundError``
-----------------------------------------

``url_for`` used to return ``None`` for an unknown endpoint or route name,
which rendered a literal ``"None"`` in templates. It now raises
``RouteNotFoundError``, a ``LookupError`` subclass::

    # v7: silently returned None
    api.url_for("no-such-route")  # -> None

    # v8: fails loudly
    try:
        url = api.url_for("no-such-route")
    except responder.RouteNotFoundError:
        url = "/fallback"

Since it subclasses ``LookupError``, existing ``except LookupError`` blocks
also catch it. Broken ``{{ url_for(...) }}`` calls in templates now surface as
errors instead of dead ``/None`` links.

Generated URLs also percent-encode parameter values now::

    api.url_for(get_file, name="a b")  # -> "/file/a%20b"

``{param:path}`` segments keep their slashes. If you were pre-encoding values
before passing them to ``url_for``, stop — they would be double-encoded.


Redirects Default to 307
------------------------

``resp.redirect()`` (and the ``api.redirect()`` helper) used to default to
``301 Moved Permanently``, which browsers cache indefinitely and legacy
clients rewrite from POST to GET. The default is now a method-preserving
``307 Temporary Redirect``::

    # v7: 301 Moved Permanently
    resp.redirect("/new-home")

    # v8: 307 Temporary Redirect
    resp.redirect("/new-home")

For a permanent, method-preserving redirect, pass ``permanent=True``::

    resp.redirect("/new-home", permanent=True)  # 308 Permanent Redirect

An explicit ``status_code=`` still takes precedence, so the old behavior is
one argument away::

    resp.redirect("/new-home", status_code=301)

If your tests assert on ``301``, update them to ``307`` (or pass an explicit
status code in the handler).


Text Responses Declare a Charset
--------------------------------

Text responses now declare their charset the standard way, as a
``Content-Type`` parameter, and the nonstandard ``Encoding`` response header
is no longer sent::

    resp.text = "hello"
    # v7: Content-Type: text/plain          + Encoding: utf-8
    # v8: Content-Type: text/plain; charset=utf-8

``resp.encoding`` is now honored when encoding ``str`` bodies for *all* text
types — previously ``resp.html`` ignored it, and ``str`` bodies fell through
to a UTF-8 encode, producing mojibake for e.g. ``latin-1``::

    resp.encoding = "latin-1"
    resp.html = "<p>café</p>"
    # v8: body encoded as latin-1, Content-Type: text/html; charset=latin-1

The charset parameter is only added when the framework actually encodes a
``str`` body; raw ``bytes`` bodies (e.g. ``resp.file()``) keep their bare
media type. If a client was reading the old ``Encoding`` header, read the
``charset`` parameter of ``Content-Type`` instead. Tests that compare
``Content-Type`` exactly (``== "text/html"``) need the charset parameter
added.


YAML Is Served as ``application/yaml``
--------------------------------------

YAML responses now use the RFC 9512 registered media type instead of the
legacy ``application/x-yaml``; the OpenAPI ``/schema.yml`` route follows
suit::

    # v7 response header: Content-Type: application/x-yaml
    # v8 response header: Content-Type: application/yaml

Inbound requests are unaffected: bodies sent with either media type still
parse as YAML, and ``Accept: application/x-yaml`` still negotiates a YAML
response. Only clients (or tests) asserting on the response's exact
``Content-Type`` need updating.


Empty YAML Bodies Return 400
----------------------------

An empty or whitespace-only YAML request body now raises ``400 Bad Request``
from ``req.media()``, matching the JSON format. Previously ``yaml.safe_load``
silently returned ``None`` and handlers had to guard against it::

    @api.post("/config")
    async def update(req, resp):
        data = await req.media(format="yaml")
        # v7: data could be None for an empty body
        # v8: an empty body never reaches here — the client gets a 400

An explicit YAML ``null`` document still parses to ``None``; only the
empty-body case is rejected.


Dependency Declarations and Floors
----------------------------------

``jinja2``, ``pyyaml``, and ``itsdangerous`` are now declared as direct
dependencies. Responder imports them directly (templates, YAML content
negotiation, and cookie-based session middleware) but previously received
them only transitively via the ``starlette[full]`` extra — so most
environments already have them installed, and a plain upgrade needs no
action.

Several floors were also raised:

- ``python-multipart>=0.0.12`` — the first release shipping the
  ``python_multipart`` module name Responder imports.
- ``apispec>=6.6`` and ``marshmallow>=3.20`` — what the OpenAPI extension is
  tested against.
- ``setuptools>=77`` (build-time only) — the first version supporting the
  PEP 639 SPDX license expression.

If your project pins one of these below its new floor, your resolver will
report a conflict at upgrade time. Raise your pin to at least the floor —
these are minimums Responder actually exercises, not preferences — or stay on
Responder 7.x until you can.
