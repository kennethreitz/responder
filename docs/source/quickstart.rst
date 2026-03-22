Quick Start
===========

This guide will walk you through the basics of building a web service with
Responder. By the end, you'll know how to declare routes, handle requests,
send responses, render templates, and process background tasks.


Create a Web Service
--------------------

The first thing you need to do is declare a web service. This is the central
object that holds all your routes, middleware, and configuration::

    import responder

    api = responder.API()


Hello World
-----------

Next, add a route. Here, we'll make the root URL say "hello, world!"::

    @api.route("/")
    def hello_world(req, resp):
        resp.text = "hello, world!"

Every view receives a ``req`` (request) and ``resp`` (response) object. You
don't need to return anything — just mutate the response directly.


Run the Server
--------------

Start your web service with ``api.run()``::

    api.run()

This spins up a production-grade uvicorn server on port ``5042``, ready for
incoming HTTP requests.

You can customize the port with ``api.run(port=8000)``. The ``PORT``
environment variable is also honored automatically — when set, Responder
binds to ``0.0.0.0`` on that port, which is what cloud platforms like
Fly.io, Railway, and Google Cloud Run expect.

.. note::

   Both sync and async views are supported. The ``async`` keyword is always
   optional — use it when you need to ``await`` something.


Route Parameters
----------------

If you want dynamic URLs, use Python's familiar f-string syntax to declare
variables in your routes::

    @api.route("/hello/{who}")
    def hello_to(req, resp, *, who):
        resp.text = f"hello, {who}!"

A ``GET`` request to ``/hello/world`` will respond with ``hello, world!``.

Route parameters are passed as keyword-only arguments (after the ``*``).


Type Convertors
^^^^^^^^^^^^^^^

You can constrain route parameters to specific types. The parameter will be
automatically converted before it reaches your view::

    @api.route("/add/{a:int}/{b:int}")
    async def add(req, resp, *, a, b):
        resp.text = f"{a} + {b} = {a + b}"

Supported types:

- ``str`` — matches any string without slashes (default)
- ``int`` — matches digits, converts to ``int``
- ``float`` — matches decimal numbers, converts to ``float``
- ``uuid`` — matches UUID strings like ``550e8400-e29b-41d4-a716-446655440000``
- ``path`` — matches any string *including* slashes, useful for file paths


Sending Responses
-----------------

Responder gives you several ways to send data back to the client. Just set
the appropriate property on the response object.

**Text and HTML**::

    resp.text = "plain text response"
    resp.html = "<h1>HTML response</h1>"

**JSON** — the most common pattern for APIs. Set ``resp.media`` to any
JSON-serializable Python object::

    @api.route("/hello/{who}/json")
    def hello_json(req, resp, *, who):
        resp.media = {"hello": who}

If the client sends an ``Accept: application/x-yaml`` header, the same data
will be returned as YAML instead. Content negotiation is automatic.

**Files** — serve a file from disk with automatic content-type detection::

    resp.file("reports/annual.pdf")

**Raw bytes**::

    resp.content = b"\x89PNG\r\n..."

**Status codes and headers**::

    resp.status_code = 201
    resp.headers["X-Custom"] = "value"

**Redirects**::

    api.redirect(resp, location="/new-url")


Reading Requests
----------------

The request object gives you access to everything the client sent.

**Method and URL**::

    req.method      # "get", "post", etc. (lowercase)
    req.full_url    # "http://example.com/path?q=1"
    req.url         # parsed URL object

**Headers** — case-insensitive, just like you'd expect::

    req.headers["Content-Type"]
    req.headers["content-type"]  # same thing

**Query parameters**::

    # GET /search?q=python&page=2
    req.params["q"]     # "python"
    req.params["page"]  # "2"

**Path parameters** — also available on the request object::

    req.path_params["user_id"]  # same as the keyword argument

**Request body** — for POST/PUT/PATCH requests, you need to ``await`` the
body content::

    # JSON body
    data = await req.media()

    # Form data
    data = await req.media("form")

    # File uploads
    files = await req.media("files")

    # Raw bytes
    body = await req.content

    # Raw text
    text = await req.text

**Other useful properties**::

    req.is_json     # True if content type is JSON
    req.cookies     # dict of cookies
    req.session     # session data (dict)
    req.client      # (host, port) tuple
    req.is_secure   # True if HTTPS


Rendering Templates
-------------------

Responder includes built-in `Jinja2 <https://jinja.palletsprojects.com/>`_
support. Templates are loaded from the ``templates/`` directory by default.

The simplest way is to use ``api.template()``::

    @api.route("/hello/{name}/html")
    def hello_html(req, resp, *, name):
        resp.html = api.template("hello.html", name=name)

You can also use the ``Templates`` class directly for more control::

    from responder.templates import Templates

    templates = Templates(directory="templates")

    @api.route("/page")
    def page(req, resp):
        resp.html = templates.render("page.html", title="Hello")

Async rendering is supported too::

    templates = Templates(directory="templates", enable_async=True)
    resp.html = await templates.render_async("page.html", title="Hello")

You can render template strings without a file::

    resp.html = api.template_string("Hello, {{ name }}!", name="world")


Background Tasks
----------------

Sometimes you want to accept a request, respond immediately, and do the
actual processing later. Responder makes this easy with background tasks::

    @api.route("/incoming")
    async def receive_incoming(req, resp):
        data = await req.media()

        @api.background.task
        def process_data(data):
            """This runs in a background thread."""
            import time
            time.sleep(10)  # simulate heavy work

        process_data(data)

        # Respond immediately — processing continues in the background
        resp.media = {"status": "accepted"}

The ``@api.background.task`` decorator wraps any function to run in a thread
pool. The client gets an immediate response while the work continues.
