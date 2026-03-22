Quick Start
===========

Create an API
-------------

::

    import responder

    api = responder.API()

Add a route
-----------

::

    @api.route("/")
    def hello(req, resp):
        resp.text = "hello, world!"

Run it
------

::

    api.run()

This starts a production uvicorn server on port ``5042``. Customize with
``api.run(port=8000)`` or set the ``PORT`` environment variable.


Route Parameters
----------------

Use f-string syntax for dynamic URLs::

    @api.route("/hello/{who}")
    def hello_to(req, resp, *, who):
        resp.text = f"hello, {who}!"

Type convertors are available::

    @api.route("/add/{a:int}/{b:int}")
    async def add(req, resp, *, a, b):
        resp.text = f"{a} + {b} = {a + b}"

Supported types: ``str``, ``int``, ``float``, ``uuid``, ``path``.


Responses
---------

::

    # Text
    resp.text = "hello"

    # HTML
    resp.html = "<h1>hello</h1>"

    # JSON (default)
    resp.media = {"hello": "world"}

    # Bytes
    resp.content = b"\x00\x01\x02"

    # File
    resp.file("report.pdf")

    # Status code
    resp.status_code = 201

    # Headers
    resp.headers["X-Custom"] = "value"

    # Redirect
    api.redirect(resp, location="/other")


Requests
--------

::

    # Method (lowercase)
    req.method  # "get", "post", etc.

    # Headers (case-insensitive)
    req.headers["Content-Type"]

    # Query parameters
    req.params["q"]

    # Path parameters
    req.path_params["user_id"]

    # JSON body (must await)
    data = await req.media()

    # Raw body
    body = await req.content

    # Check content type
    req.is_json  # True/False

    # Client address
    req.client  # (host, port)


Templates
---------

Responder includes Jinja2 templating::

    @api.route("/hello/{name}/html")
    def hello_html(req, resp, *, name):
        resp.html = api.template("hello.html", name=name)

Or use the ``Templates`` class directly::

    from responder.templates import Templates

    templates = Templates(directory="templates")
    resp.html = templates.render("page.html", title="Hello")


Background Tasks
----------------

Process work in the background while responding immediately::

    @api.route("/work")
    async def work(req, resp):
        data = await req.media()

        @api.background.task
        def process(data):
            import time
            time.sleep(10)

        process(data)
        resp.media = {"status": "processing"}
