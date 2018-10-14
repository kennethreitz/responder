Quick Start
===========

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
