.. responder documentation master file, created by
   sphinx-quickstart on Thu Oct 11 12:58:34 2018.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

A familiar HTTP Service Framework
=================================

|Build Status| |image1| |image2| |image3| |image4| |image5|

.. |Build Status| image:: https://travis-ci.org/kennethreitz/responder.svg?branch=master
   :target: https://travis-ci.org/kennethreitz/responder
.. |image1| image:: https://img.shields.io/pypi/v/responder.svg
   :target: https://pypi.org/project/responder/
.. |image2| image:: https://img.shields.io/pypi/l/responder.svg
   :target: https://pypi.org/project/responder/
.. |image3| image:: https://img.shields.io/pypi/pyversions/responder.svg
   :target: https://pypi.org/project/responder/
.. |image4| image:: https://img.shields.io/github/contributors/kennethreitz/responder.svg
   :target: https://github.com/kennethreitz/responder/graphs/contributors
.. |image5| image:: https://img.shields.io/badge/Say%20Thanks-!-1EAEDB.svg
   :target: https://saythanks.io/to/kennethreitz

The Python world certainly doesn't need more web frameworks. But, it does need more creativity, so I thought I'd
spread some `Hacktoberfest <https://hacktoberfest.digitalocean.com/>`_ spirit around, bring some of my ideas to the table, and see what I could come up with.

An Example Web Service:
-----------------------

.. code:: python

   import responder

   api = responder.API()

   @api.route("/{greeting}")
   async def greet_world(req, resp, *, greeting):
       resp.text = f"{greeting}, world!"

   if __name__ == '__main__':
       api.run()

That ``async`` declaration is optional.

This gets you a ASGI app, with a production static files server
pre-installed, jinja2 templating (without additional imports), and a
production webserver based on uvloop, serving up requests with gzip
compression automatically.

Testimonials
------------

   “Pleasantly very taken with python-responder.
   `@kennethreitz <https://twitter.com/kennethreitz>`_ at his absolute
   best.” —Rudraksh M.K.

..

   “Buckle up!” —Tom Christie of `APIStar`_ and `Django REST Framework`_

   “I love that you are exploring new patterns. Go go go!” — Danny
   Greenfield, author of `Two Scoops of Django`_

..

   “Love what I have seen while it’s in progress! Many features of
   Responder are from my wishlist for Flask, and it’s even faster and
   even easier than Flask!” — Luna C.


..


   “The most ambitious crossover event in history.” —Pablo Cabezas, `on
   Tom Christie joining the project`_

.. _APIStar: https://github.com/encode/apistar
.. _Django REST Framework: https://www.django-rest-framework.org/
.. _Two Scoops of Django:
.. _on Tom Christie joining the project: https://twitter.com/pabloteleco/status/1050841098321620992?s=20

More Examples
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

Here, you can spawn off a background thread to run any function, out-of-request::

    @api.route("/")
    def hello(req, resp):

        @api.background.task
        def sleep(s=10):
            time.sleep(s)
            print("slept!")

        sleep()
        resp.content = "processing"


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

Performance
-----------

::

    python-responder v0.0.1 [stats]
    Requests/sec:    952.54
    Transfer/sec:    119.07KB

    Django v2.1.2 (i18n == False) [stats]
    Requests/sec:    520.87
    Transfer/sec:     98.68KB

The Basic Idea
--------------

The primary concept here is to bring the nicities that are brought forth from both Flask and Falcon and unify them into a single framework, along with some new ideas I have. I also wanted to take some of the API primitives that are instilled in the Requests library and put them into a web framework. So, you'll find a lot of parallels here with Requests.

- Setting ``resp.text`` sends back unicode, while setting ``resp.content`` sends back bytes.
- Setting `resp.media` sends back JSON/YAML (``.text``/``.content`` override this).
- Case-insensitive ``req.headers`` dict (from Requests directly).
- ``resp.status_code``, ``req.method``, ``req.url``, and other familiar friends.

Ideas
-----

- Flask-style route expression, with new capabilities -- all while using Python 3.6+'s new f-string syntax.
- I love Falcon's "every request and response is passed into to each view and mutated" methodology, especially ``response.media``, and have used it here. In addition to supporting JSON, I have decided to support YAML as well, as Kubernetes is slowly taking over the world, and it uses YAML for all the things. Content-negotiation and all that.
- **A built in testing client that uses the actual Requests you know and love**.
- The ability to mount other WSGI apps easily.
- Automatic gzipped-responses.
- In addition to Falcon's ``on_get``, ``on_post``, etc methods, Responder features an ``on_request`` method, which gets called on every type of request, much like Requests.
- A production static files server is built-in.
- Uvicorn built-in as a production web server. I would have chosen Gunicorn, but it doesn't run on Windows. Plus, Uvicorn serves well to protect against slowloris attacks, making nginx unneccessary in production.
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
