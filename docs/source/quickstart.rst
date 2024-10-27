Quick Start!
============

This section of the documentation exists to provide an introduction to the Responder interface,
as well as educate the user on basic functionality.


Declare a Web Service
---------------------

The first thing you need to do is declare a web service::

    import responder

    api = responder.API()

Hello World!
------------

Then, you can add a view / route to it.

Here, we'll make the root URL say "hello world!"::

    @api.route("/")
    def hello_world(req, resp):
        resp.text = "hello, world!"

Run the Server
--------------

Next, we can run our web service easily, with ``api.run()``::

    api.run()

This will spin up a production web server on port ``5042``, ready for incoming HTTP requests.

Note: you can pass ``port=5000`` if you want to customize the port. The ``PORT`` environment variable for established web service providers (e.g. Heroku) will automatically be honored and will set the listening address to ``0.0.0.0`` automatically (also configurable through the ``address`` keyword argument).


Accept Route Arguments
----------------------

If you want dynamic URLs, you can use Python's familiar *f-string syntax* to declare variables in your routes::

    @api.route("/hello/{who}")
    def hello_to(req, resp, *, who):
        resp.text = f"hello, {who}!"

A ``GET`` request to ``/hello/brettcannon`` will result in a response of ``hello, brettcannon!``.

Type convertors are also available::

    @api.route("/add/{a:int}/{b:int}")
    async def add(req, resp, *, a, b):
        resp.text = f"{a} + {b} = {a + b}"

Supported types: ``str``, ``int`` and ``float``.

Returning JSON / YAML
---------------------

If you want your API to send back JSON, simply set the ``resp.media`` property to a JSON-serializable Python object::


    @api.route("/hello/{who}/json")
    def hello_to(req, resp, *, who):
        resp.media = {"hello": who}

A ``GET`` request to ``/hello/guido/json`` will result in a response of ``{'hello': 'guido'}``.

If the client requests YAML instead (with a header of ``Accept: application/x-yaml``), YAML will be sent.

Rendering a Template
--------------------

Responder provides a built-in light `Jinja`_ wrapper ``templates.Templates``

Usage::

  from responder.templates import Templates

  templates = Templates()

  @api.route("/hello/{name}/html")
  def hello(req, resp, name):
      resp.html = templates.render("hello.html", name=name)


Also a ``render_async`` is available::

    templates = Templates(enable_async=True)
    resp.html = await templates.render_async("hello.html", who=who)

You can also use the existing ``api.template(filename, *args, **kwargs)`` to render templates::

    @api.route("/hello/{who}/html")
    def hello_html(req, resp, *, who):
        resp.html = api.template('hello.html', who=who)


Setting Response Status Code
----------------------------

If you want to set the response status code, simply set ``resp.status_code``::

    @api.route("/416")
    def teapot(req, resp):
        resp.status_code = api.status_codes.HTTP_416   # ...or 416


Setting Response Headers
------------------------

If you want to set a response header, like ``X-Pizza: 42``, simply modify the ``resp.headers`` dictionary::

    @api.route("/pizza")
    def pizza_pizza(req, resp):
        resp.headers['X-Pizza'] = '42'

That's it!


Receiving Data & Background Tasks
---------------------------------

If you're expecting to read any request data, on the server, you need to declare your view as async and await the content.

Here, we'll process our data in the background, while responding immediately to the client::

    import time

    @api.route("/incoming")
    async def receive_incoming(req, resp):

        @api.background.task
        def process_data(data):
            """Just sleeps for three seconds, as a demo."""
            time.sleep(3)


        # Parse the incoming data as form-encoded.
        # Note: 'json' and 'yaml' formats are also automatically supported.
        data = await req.media()

        # Process the data (in the background).
        process_data(data)

        # Immediately respond that upload was successful.
        resp.media = {'success': True}

A ``POST`` request to ``/incoming`` will result in an immediate response of ``{'success': true}``.


Here's a sample code to post a file with background::

    @api.route("/")
    async def upload_file(req, resp):

        @api.background.task
        def process_data(data):
            f = open('./{}'.format(data['file']['filename']), 'w')
            f.write(data['file']['content'].decode('utf-8'))
            f.close()

        data = await req.media(format='files')
        process_data(data)

        resp.media = {'success': 'ok'}

You can send a file easily with requests::

	  import requests

	  data = {'file': ('hello.txt', 'hello, world!', "text/plain")}
	  r = requests.post('http://127.0.0.1:8210/file', files=data)

	  print(r.text)


.. _Jinja: https://jinja.palletsprojects.com/en/stable/
