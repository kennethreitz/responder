Migrating from Flask
====================

If you're coming from Flask, you'll find Responder familiar but different
in a few key ways. This guide maps Flask concepts to their Responder
equivalents and shows you how to translate common patterns.


The Big Differences
-------------------

**Mutate the response, or return one.** In Flask you return a response.
Responder hands you a ``resp`` object to mutate — that's the idiomatic style,
and it keeps the response editable through hooks and middleware:

Flask::

    @app.route("/")
    def hello():
        return "hello, world!"

Responder::

    @api.route("/")
    def hello(req, resp):
        resp.text = "hello, world!"

Flask-style returns work too. A string becomes the body, a dict or list
becomes JSON, a Pydantic model or dataclass is serialized for you, and a
``(body, status[, headers])`` tuple sets the lot at once::

    @api.route("/")
    def hello(req, resp):
        return "hello, world!"

    @api.route("/items", methods=["POST"])
    async def create(req, resp):
        return {"created": True}, 201

See :ref:`accepted return types <returning-values>` for the complete list.

**Explicit request and response.** Flask uses a global ``request`` object
(via thread-local magic). Responder passes ``req`` and ``resp`` explicitly.
No magic, no import needed — they're right there in the function signature.

**ASGI, not WSGI.** Flask runs on WSGI, which is synchronous. Responder
runs on ASGI, which supports async natively. You can still write sync
views — Responder runs them in a thread pool automatically.


Quick Reference
---------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Flask
     - Responder
   * - ``Flask(__name__)``
     - ``responder.API()``
   * - ``return "text"``
     - ``resp.text = "text"``
   * - ``return jsonify(data)``
     - ``resp.media = data``
   * - ``return render_template("t.html", x=1)``
     - ``resp.html = api.template("t.html", x=1)``
   * - ``request.args["q"]``
     - ``req.params["q"]``
   * - ``request.json``
     - ``await req.media()``
   * - ``request.form``
     - ``await req.media("form")``
   * - ``request.headers["X"]``
     - ``req.headers["X"]``
   * - ``request.method``
     - ``req.method``
   * - ``request.cookies["x"]``
     - ``req.cookies["x"]``
   * - ``session["x"] = 1``
     - ``resp.session["x"] = 1``
   * - ``abort(404)``
     - ``responder.abort(404)``
   * - ``redirect("/new")``
     - ``api.redirect(resp, location="/new")``
   * - ``@app.before_request``
     - ``@api.before_request``
   * - ``@app.errorhandler(404)``
     - ``@api.exception_handler(404)``
   * - ``app.run(debug=True)``
     - ``api.run(debug=True)``


A few of these mappings deserve a closer look:

- ``responder.abort(404)`` *raises* and halts the handler, exactly like
  Flask's ``abort()`` — assigning ``resp.status_code`` alone does not stop
  execution. Import it from ``responder``; it works from handlers, hooks, and
  dependencies alike.
- ``@api.exception_handler`` takes an exception class *or* a status code, so
  ``@api.exception_handler(404)`` mirrors ``@app.errorhandler(404)``. The
  handler receives ``(req, resp, exc)``.
- **Sessions need a secret key.** ``resp.session`` and ``req.session`` ride on
  a signed cookie, so Responder needs a signing key: set ``API(secret_key=...)``
  or the ``RESPONDER_SECRET_KEY`` environment variable. Without one, a random
  per-process key is minted (with a loud warning), so sessions won't survive a
  restart or span multiple workers; ``secret_key="NOTASECRET"`` is rejected, and
  cookies are marked ``Secure`` in production. See :doc:`guide-config`.
- **Redirects can block open redirects.** Pass ``allow_external=False`` to
  ``api.redirect`` when the location comes from user input (e.g. a ``?next=``
  parameter).


Route Parameters
----------------

Flask uses ``<angle_brackets>``. Responder uses ``{curly_braces}``
with the same type convertor idea:

Flask::

    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        return jsonify({"id": user_id})

Responder::

    @api.route("/users/{user_id:int}")
    def get_user(req, resp, *, user_id):
        resp.media = {"id": user_id}

Note the ``*`` — route parameters are keyword-only arguments in
Responder. This makes the interface explicit about which arguments
come from the URL.

Query-string parameters can be typed the same way. Instead of reaching into
``req.params``, declare them with the ``Query`` marker and Responder coerces
the value — returning ``422`` if it's missing or the wrong type::

    from responder import Query

    @api.route("/search")
    def search(req, resp, *, q: str = Query(...), limit: int = Query(10)):
        resp.media = {"q": q, "limit": limit}

``Query(...)`` is required; ``Query(10)`` supplies a default. There are
matching ``Header``, ``Cookie``, and ``Path`` markers too.


JSON APIs
---------

Flask::

    @app.route("/api/items", methods=["POST"])
    def create_item():
        data = request.json
        # ... create item
        return jsonify(item), 201

Responder::

    @api.route("/api/items", methods=["POST"])
    async def create_item(req, resp):
        data = await req.media()
        # ... create item
        return item, 201

The ``await`` is needed because reading the request body is async I/O — the
event loop stays free while the body arrives. The ``return item, 201`` is the
Flask-style tuple from earlier; you could just as well mutate ``resp.media``
and ``resp.status_code``.

**Skip the manual parsing.** Flask hands you ``request.json`` and leaves
validation to you. Responder can do better: annotate a keyword-only parameter
with a Pydantic model and the body is parsed, validated, and injected for you —
with an automatic ``422`` (and the validation errors) when it doesn't fit::

    from pydantic import BaseModel

    class ItemIn(BaseModel):
        name: str
        price: float

    @api.route("/api/items", methods=["POST"])
    async def create_item(req, resp, *, item: ItemIn):
        # `item` is a validated ItemIn — no try/except needed
        return {"id": 1, "name": item.name}, 201

The same idea runs in reverse: a Pydantic *return* annotation becomes the
response model, validating and trimming the payload on the way out. See
:doc:`tutorial-rest` for the full typed-I/O walkthrough.


Templates
---------

Both use Jinja2. The syntax is nearly identical:

Flask::

    @app.route("/hello/<name>")
    def hello(name):
        return render_template("hello.html", name=name)

Responder::

    @api.route("/hello/{name}")
    def hello(req, resp, *, name):
        resp.html = api.template("hello.html", name=name)


Blueprints → Routers
--------------------

Flask uses Blueprints to organize routes across modules. Responder's
standalone :class:`responder.Router` works the same way — declare routes in
a module without an app instance, then register the router on the app:

Flask::

    bp = Blueprint("api", __name__, url_prefix="/api")

    @bp.route("/users")
    def list_users():
        return jsonify([])

    app.register_blueprint(bp)

Responder::

    from responder import Router

    router = Router(prefix="/api")

    @router.route("/users")
    def list_users(req, resp):
        resp.media = []

    api.include_router(router)

Routers nest, take an extra prefix at inclusion time, and carry group-level
``tags``, ``dependencies``, and ``auth`` — see :doc:`routers`. For quick
same-file prefix grouping there is also ``api.group("/api")``, which
registers routes immediately on the live ``api``.


Gradual Migration
-----------------

You don't have to migrate all at once. Responder can mount your existing
Flask app at a subroute, so you can move endpoints over one at a time::

    from flask import Flask

    flask_app = Flask(__name__)

    # Your existing Flask routes stay here
    @flask_app.route("/legacy")
    def legacy():
        return "old endpoint"

    # Mount Flask under /old, new routes go on Responder
    api.mount("/old", flask_app)

    @api.route("/new")
    def new_endpoint(req, resp):
        resp.media = {"modern": True}

Requests to ``/old/legacy`` go to Flask. Everything else goes to
Responder. When you've moved everything over, remove the mount.
