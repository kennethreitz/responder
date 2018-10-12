.. responder documentation master file, created by
   sphinx-quickstart on Thu Oct 11 12:58:34 2018.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

A familar HTTP Service Framework
================================

|Build Status| |image| |image| |image| |image| |image|

.. |Build Status| image:: https://travis-ci.org/kennethreitz/responder.svg?branch=master
   :target: https://travis-ci.org/kennethreitz/responder
.. |image| image:: https://img.shields.io/pypi/v/responder.svg
   :target: https://pypi.org/project/responder/
.. |image| image:: https://img.shields.io/pypi/l/responder.svg
   :target: https://pypi.org/project/responder/
.. |image| image:: https://img.shields.io/pypi/pyversions/responder.svg
   :target: https://pypi.org/project/responder/
.. |image| image:: https://img.shields.io/github/contributors/kennethreitz/responder.svg
   :target: https://github.com/kennethreitz/responder/graphs/contributors
.. |image| image:: https://img.shields.io/badge/Say%20Thanks-!-1EAEDB.svg
   :target: https://saythanks.io/to/kennethreitz

The Python world certainly doesn't need more web frameworks. But, it does need more creativity, so I thought I'd
spread some `Hacktoberfest <https://hacktoberfest.digitalocean.com/>`_ spirit around, bring some of my ideas to the table, and see what I could come up with.

But will it blend?
------------------

::

    import responder

    api = responder.API()

    @api.route("/{greeting}")
    def greet_world(req, resp, *, greeting):
        resp.text = f"{greeting}, world!"

    if __name__ == '__main__':
        api.run()


This gets you a WSGI app, with WhiteNoise pre-installed, jinja2 templating (without additional imports), and a production webserver (ready for slowloris attacks), serving up requests with gzip compression automatically.

-------------

Class-based views (and setting some headers and stuff)::

    @api.route("/{greeting}")
    class GreetingResource:
        def on_request(req, resp, *, greeting):   # or on_get...
            resp.text = f"{greeting}, world!"
            resp.headers.update({'X-Life': '42'})
            resp.status_code = api.status_codes.HTTP_416


Render a template, with arguments::


    @api.route("/{greeting}")
    def greet_world(req, resp, *, greeting):
        resp.content = api.template("index.html", greeting=greeting)


The ``api`` instance is available as an object during template rendering.

Serve a GraphQL API::

    import graphene

    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))

        def resolve_hello(self, info, name):
            return "Hello " + name

    api.add_route("/graph", graphene.Schema(query=Query))


We can then send a query to our service::

    >>> requests = api.session()
    >>> r = requests.get("http://;/graph", params={"query": "{ hello }"})
    >>> r.json()
    {'data': {'hello': 'Hello stranger'}}


Or, request YAML back::

    >>> r = requests.get("http://;/graph", params={"query": "{ hello(name:\"john\") }"}, headers={"Accept": "application/x-yaml"})
    >>> print(r.text)
    data: {hello: Hello john}



Want HSTS?

::

    api = responder.API(enable_hsts=True)


Boom. ✨🍰✨


The Basic Idea
--------------

The primary concept here is to bring the nicities that are brought forth from both Flask and Falcon and unify them into a single framework, along with some new ideas I have. I also wanted to take some of the API primitives that are instilled in the Requests library and put them into a web framework. So, you'll find a lot of parallels here with Requests.

- Setting `resp.text` sends back unicode, while setting `resp.content` sends back bytes.
- Setting `resp.media` sends back JSON/YAML (`.text`/`.content` override this).
- Case-insensitive `req.headers` dict (from Requests directly).
- `resp.status_code`, `req.method`, `req.url`, and other familar friends.

Ideas
-----

- Flask-style route expression, with new capabilities -- primarily, the ability to cast a parameter to integers as well as other types that are missing from Flask, all while using Python 3.6+'s new f-string syntax.
- I love Falcon's "every request and response is passed into to each view and mutated" methodology, especially `response.media`, and have used it here. In addition to supporting JSON, I have decided to support YAML as well, as Kubernetes is slowly taking over the world, and it uses YAML for all the things. Content-negotiation and all that.
- **A built in testing client that uses the actual Requests you know and love**.
- The ability to mount other WSGI apps easily.
- Automatic gzipped-responses.
- In addition to Falcon's ``on_get``, ``on_post``, etc methods, Responder features an `on_request` method, which gets called on every type of request, much like Requests.
- WhiteNoise is built-in, for serving static files.
- Waitress built-in as a production web server. I would have chosen Gunicorn, but it doesn't run on Windows. Plus, Waitress serves well to protect against slowloris attacks, making nginx unneccessary in production.
- GraphQL support, via Graphene. The goal here is to have any GraphQL query exposable at any route, magically.


Future Ideas
------------

- Cooke-based sessions are currently an afterthrought, as this is an API framework, but websites are APIs too.
- Potentially support ASGI instead of WSGI. Will the tradeoffs be worth it? This is a question to ask. Procedural code works well for 90% use cases.
- If frontend websites are supported, provide an official way to run webpack.


Installation
============

.. code-block:: shell

    $ pipenv install responder
    ✨🍰✨

Only **Python 3.6+** is supported.


API Documentation
=================


Web Service (API) Class
-----------------------
.. module:: responder

.. autoclass:: API
    :inherited-members:

Requests & Responses
--------------------


.. autoclass:: Request
    :inherited-members:

.. autoclass:: Response
    :inherited-members:


Utility Functions
-----------------

.. autofunction:: responder.API.status_codes.is_100

.. autofunction:: responder.API.status_codes.is_200

.. autofunction:: responder.API.status_codes.is_300

.. autofunction:: responder.API.status_codes.is_400

.. autofunction:: responder.API.status_codes.is_500

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
