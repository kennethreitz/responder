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

Note: you can pass ``port=5000`` if you want to customize the port. The ``PORT`` environment variable for established web service providers (e.g. Heroku) will automatically be honored.


Accept Route Arguments
----------------------

If you want dynamic URLs, you can use Python's familiar *f-string syntax* to declare variables in your routes::

    @api.route("/hello/{who}")
    def hello_to(req, resp, *, who):
        resp.text = f"hello, {who}!"

A ``GET`` request to ``/hello/brettcannon`` will result in a response of ``hello, brettcannon!``.

Returning JSON / YAML
---------------------

If you want your API to send back JSON, simply set the ``resp.media`` property to a JSON-serializable Python object::


    @api.route("/hello/{who}/json")
    def hello_to(req, resp, *, who):
        resp.media = {"hello", who}

A ``GET`` request to ``/hello/guido/json`` will result in a response of ``{'hello': 'guido'}``.

If the client requests YAML instead (with a header of ``Accept: application/x-yaml``), YAML will be sent.


Setting Response Status Code
----------------------------

If you want to set the response status code, simply set ``resp.status_code``::

    @api.route("/416")
    def teapot(req, resp):
        resp.status_code = api.status_codes.HTTP_416   # ...or 416


Setting Response Headers
------------------------

If you want to set a response header, like ``X-Pizza: 42``, simply modify the ``resp.headers`` dictionary:

    @api.route("/pizza")
    def pizza_pizza(req, resp):
        resp.headers['X-Pizza'] = 42

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
        # Note: 'json' and 'yaml' formats are also supported.
        data = await resp.media('form')

        # Process the data (in the background).
        process_data(data)

        # Immediately respond that upload was successful.
        resp.media = {'success': True}

A ``POST`` request to ``/incoming`` will result in an immediate response of ``{'success': true}``.
