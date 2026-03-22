Quick Start
===========

This guide will walk you through the basics of building a web service with
Responder. By the end, you'll understand how HTTP requests and responses
work, how to define routes, read data from clients, send data back, render
HTML templates, and process work in the background.


Create a Web Service
--------------------

Every web application starts with a single object — the application
instance. In Responder, this is the ``API`` class. It holds your routes,
middleware, templates, and configuration. Think of it as the central
nervous system of your web service::

    import responder

    api = responder.API()

That's it. One import, one line. You now have a fully functional ASGI
application with gzip compression, static file serving, session support,
and a production-ready server — all wired up and ready to go.


Hello World
-----------

A web service isn't very useful until it can respond to requests. In HTTP,
a *route* maps a URL path to a function that handles it. When a client
(like a browser or ``curl``) sends a request to that path, your function
runs and produces a response.

Here's the simplest possible route::

    @api.route("/")
    def hello_world(req, resp):
        resp.text = "hello, world!"

Two things to notice:

1. Every view function receives two arguments: ``req`` (the incoming
   request) and ``resp`` (the outgoing response).
2. You don't return anything. Instead, you *mutate* the response object
   directly. This is a deliberate design choice — it keeps the API
   consistent whether you're setting text, JSON, headers, cookies, or
   status codes.


Run the Server
--------------

Start your web service with a single call::

    api.run()

This spins up a production-grade `uvicorn <https://www.uvicorn.org/>`_
server on port ``5042``, ready for incoming HTTP requests. Open
``http://localhost:5042`` in your browser and you'll see your hello world
response.

You can customize the port with ``api.run(port=8000)``. The ``PORT``
environment variable is also honored automatically — when set, Responder
binds to ``0.0.0.0`` on that port, which is what cloud platforms expect.

.. note::

   Both sync and async views are supported. The ``async`` keyword is always
   optional — use it when you need to ``await`` something, like reading a
   request body or querying a database.


Route Parameters
----------------

Static URLs like ``/about`` are useful, but most applications need dynamic
routes — URLs that contain variable data, like a user ID or a product slug.

In Responder, you declare route parameters using Python's f-string syntax::

    @api.route("/hello/{who}")
    def hello_to(req, resp, *, who):
        resp.text = f"hello, {who}!"

A ``GET`` request to ``/hello/world`` will respond with ``hello, world!``.
A request to ``/hello/guido`` will respond with ``hello, guido!``.

Route parameters are passed as *keyword-only* arguments (after the ``*``
in the function signature). This is a Python feature that makes the
interface explicit — you always know which arguments come from the URL.


Type Convertors
^^^^^^^^^^^^^^^

By default, route parameters are strings. But often you want them as
integers, UUIDs, or other types. Responder can convert them automatically
using type annotations in the route pattern::

    @api.route("/add/{a:int}/{b:int}")
    async def add(req, resp, *, a, b):
        resp.text = f"{a} + {b} = {a + b}"

Here, ``a`` and ``b`` will arrive as Python ``int`` objects, not strings.
If someone requests ``/add/3/hello``, they'll get a 404 — the route won't
match because ``hello`` isn't a valid integer.

Supported types:

- ``str`` — matches any string without slashes (this is the default)
- ``int`` — matches digits and converts to ``int``
- ``float`` — matches decimal numbers and converts to ``float``
- ``uuid`` — matches UUID strings like ``550e8400-e29b-41d4-a716-446655440000``
- ``path`` — matches any string *including* slashes, useful for file paths
  like ``/files/{filepath:path}``


Sending Responses
-----------------

When an HTTP server receives a request, it must send back a response. Every
HTTP response has three parts: a status code (like ``200 OK`` or ``404 Not
Found``), headers (metadata like ``Content-Type``), and a body (the actual
data).

Responder lets you set all three by mutating the response object.

**Text and HTML** — the simplest response types. ``resp.text`` sets the
``Content-Type`` to ``text/plain``, while ``resp.html`` sets it to
``text/html``::

    resp.text = "plain text response"
    resp.html = "<h1>HTML response</h1>"

**JSON** — the lingua franca of web APIs. Set ``resp.media`` to any
JSON-serializable Python object — a dict, a list, whatever — and Responder
will serialize it to JSON and set the right headers::

    @api.route("/hello/{who}/json")
    def hello_json(req, resp, *, who):
        resp.media = {"hello": who}

If the client sends an ``Accept: application/x-yaml`` header, the same data
will be returned as YAML instead. This is called *content negotiation* —
the server and client agree on a format. It happens automatically.

**Files** — serve a file from disk. Responder uses Python's ``mimetypes``
module to figure out the ``Content-Type`` from the file extension::

    resp.file("reports/annual.pdf")

**Raw bytes** — for binary data like images or protocol buffers::

    resp.content = b"\x89PNG\r\n..."

**Status codes** — HTTP status codes tell the client what happened. ``200``
means success, ``201`` means something was created, ``404`` means not found,
``500`` means the server broke. Set it directly::

    resp.status_code = 201

**Headers** — HTTP headers carry metadata. Common ones include
``Content-Type``, ``Cache-Control``, ``Authorization``, and custom
application headers::

    resp.headers["X-Custom"] = "value"

**Redirects** — tell the client to go somewhere else::

    api.redirect(resp, location="/new-url")

This sends a ``301 Moved Permanently`` response by default. The client's
browser will automatically follow the redirect.


Reading Requests
----------------

The other half of HTTP is the request — the data the client sends to your
server. This includes the HTTP method (GET, POST, PUT, DELETE), the URL,
headers, query parameters, cookies, and optionally a body.

Responder wraps all of this in the ``req`` object.

**Method and URL** — every HTTP request has a method (what the client wants
to do) and a URL (what resource it's about)::

    req.method      # "get", "post", etc. (lowercase)
    req.full_url    # "http://example.com/path?q=1"
    req.url         # parsed URL object

**Headers** — HTTP headers carry metadata from the client, like what
content types it accepts, authentication tokens, and more. Responder's
headers dict is case-insensitive, because the HTTP spec says header names
are case-insensitive::

    req.headers["Content-Type"]
    req.headers["content-type"]  # same thing

**Query parameters** — the part of the URL after the ``?``. These are
commonly used for search, filtering, and pagination::

    # GET /search?q=python&page=2
    req.params["q"]     # "python"
    req.params["page"]  # "2"

Note that query parameters are always strings. If you need an integer,
you'll need to convert it yourself: ``int(req.params["page"])``.

**Path parameters** — the dynamic parts of the URL that matched your route
pattern. These are also available on the request object, which is useful
in before-request hooks where they aren't passed as function arguments::

    req.path_params["user_id"]  # same as the keyword argument

**Request body** — for POST, PUT, and PATCH requests, the client sends
data in the body. Since reading the body is an I/O operation, you need to
``await`` it::

    # JSON body (the most common format for APIs)
    data = await req.media()

    # Form data (from HTML forms)
    data = await req.media("form")

    # File uploads (multipart)
    files = await req.media("files")

    # Raw bytes
    body = await req.content

    # Raw text
    text = await req.text

**Other useful properties**::

    req.is_json     # True if the content type is JSON
    req.cookies     # dict of cookies sent by the client
    req.session     # session data (a signed, server-side dict)
    req.client      # (host, port) tuple — the client's IP address
    req.is_secure   # True if the request came over HTTPS


Rendering Templates
-------------------

While APIs typically return JSON, many web applications need to render
HTML pages. Responder includes built-in support for
`Jinja2 <https://jinja.palletsprojects.com/>`_, one of the most popular
templating engines in the Python ecosystem.

Templates let you write HTML with placeholders that get filled in with
dynamic data. This keeps your presentation logic (HTML) separate from
your application logic (Python) — a pattern called
*separation of concerns*.

The simplest way to render a template is ``api.template()``. Templates
are loaded from the ``templates/`` directory by default::

    @api.route("/hello/{name}/html")
    def hello_html(req, resp, *, name):
        resp.html = api.template("hello.html", name=name)

The template file ``templates/hello.html`` might look like::

    <h1>Hello, {{ name }}!</h1>

The ``{{ name }}`` part is a Jinja2 expression — it gets replaced with
the value you passed in.

You can also use the ``Templates`` class directly for more control over
the template directory and configuration::

    from responder.templates import Templates

    templates = Templates(directory="my_templates")

    @api.route("/page")
    def page(req, resp):
        resp.html = templates.render("page.html", title="Hello")

For applications that need non-blocking template rendering (rare, but
useful under extreme load), async rendering is supported::

    templates = Templates(directory="templates", enable_async=True)
    resp.html = await templates.render_async("page.html", title="Hello")

And for quick one-off templates, you can render a string directly without
a file::

    resp.html = api.template_string("Hello, {{ name }}!", name="world")


Background Tasks
----------------

Sometimes you want to accept a request, respond immediately, and do the
actual processing later. This is a common pattern for operations that take
a long time — sending emails, processing images, updating caches, or
calling slow external APIs.

Responder makes this easy with background tasks. Decorate any function
with ``@api.background.task`` and it will run in a thread pool, separate
from the request/response cycle::

    @api.route("/incoming")
    async def receive_incoming(req, resp):
        data = await req.media()

        @api.background.task
        def process_data(data):
            """This runs in a background thread."""
            import time
            time.sleep(10)  # simulate heavy work

        process_data(data)

        # This response is sent immediately, while process_data
        # continues running in the background.
        resp.media = {"status": "accepted"}

The client gets an instant response — the heavy lifting happens after.
This is the same pattern used by task queues like Celery, but much simpler
for lightweight use cases where you don't need a full message broker.

.. note::

   Background tasks run in threads, not processes. They share memory with
   your application, which makes them fast to start but means CPU-intensive
   work will block the event loop. For heavy computation, consider a proper
   task queue.
