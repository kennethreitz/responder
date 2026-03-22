Migrating from Flask
====================

If you're coming from Flask, you'll find Responder familiar but different
in a few key ways. This guide maps Flask concepts to their Responder
equivalents and shows you how to translate common patterns.


The Big Differences
-------------------

**No return values.** In Flask, you return a response. In Responder, you
mutate it. This is the single biggest difference:

Flask::

    @app.route("/")
    def hello():
        return "hello, world!"

Responder::

    @api.route("/")
    def hello(req, resp):
        resp.text = "hello, world!"

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
     - ``resp.status_code = 404``
   * - ``redirect("/new")``
     - ``api.redirect(resp, location="/new")``
   * - ``@app.before_request``
     - ``@api.route(before_request=True)``
   * - ``@app.errorhandler(404)``
     - ``@api.exception_handler(ValueError)``
   * - ``app.run(debug=True)``
     - ``api.run(debug=True)``


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
        resp.media = item
        resp.status_code = 201

The ``await`` is needed because reading the request body is an async
I/O operation. This is more explicit than Flask's approach, and it
means the event loop isn't blocked while waiting for the body to arrive.


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


Blueprints → Route Groups
--------------------------

Flask uses Blueprints to organize routes. Responder has route groups:

Flask::

    bp = Blueprint("api", __name__, url_prefix="/api")

    @bp.route("/users")
    def list_users():
        return jsonify([])

    app.register_blueprint(bp)

Responder::

    api_v1 = api.group("/api")

    @api_v1.route("/users")
    def list_users(req, resp):
        resp.media = []


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
