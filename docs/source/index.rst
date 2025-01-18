.. responder documentation master file, created by
   sphinx-quickstart on Thu Oct 11 12:58:34 2018.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

A familiar HTTP Service Framework
=================================

|ci-tests| |version| |license| |python-versions| |downloads| |contributors| |say-thanks|

.. |ci-tests| image:: https://github.com/kennethreitz/responder/actions/workflows/test.yaml/badge.svg
   :target: https://github.com/kennethreitz/responder/actions/workflows/test.yaml
.. |ci-docs| image:: https://github.com/kennethreitz/responder/actions/workflows/docs.yaml/badge.svg
   :target: https://github.com/kennethreitz/responder/actions/workflows/docs.yaml
.. |version| image:: https://img.shields.io/pypi/v/responder.svg
   :target: https://pypi.org/project/responder/
.. |license| image:: https://img.shields.io/pypi/l/responder.svg
   :target: https://pypi.org/project/responder/
.. |python-versions| image:: https://img.shields.io/pypi/pyversions/responder.svg
   :target: https://pypi.org/project/responder/
.. |downloads| image:: https://static.pepy.tech/badge/responder/month
   :target: https://www.pepy.tech/projects/responder
.. |contributors| image:: https://img.shields.io/github/contributors/kennethreitz/responder.svg
   :target: https://github.com/kennethreitz/responder/graphs/contributors
.. |say-thanks| image:: https://img.shields.io/badge/Say%20Thanks-!-1EAEDB.svg
   :target: https://saythanks.io/to/kennethreitz

.. code:: python

   import responder

   api = responder.API()

   @api.route("/{greeting}")
   async def greet_world(req, resp, *, greeting):
       resp.text = f"{greeting}, world!"

   if __name__ == '__main__':
       api.run()

Powered by `Starlette`_. That ``async`` declaration is optional.

The little program demonstrates an `ASGI`_ application using `Responder`_,
including production-ready components like the `uvicorn`_ webserver, based
on `uvloop`_, the static files server `ServeStatic`_, and the `Jinja`_
templating library pre-installed.

Features
--------

- A pleasant API, with a single import statement.
- Class-based views without inheritance.
- `ASGI`_, the future of Python web services.
- Asynchronous Python frameworks and applications.
- Automatic gzip compression.
- WebSocket support!
- The ability to mount any ASGI / WSGI app at a subroute.
- `f-string syntax`_ route declaration.
- Mutable response object, passed into each view. No need to return anything.
- Background tasks, spawned off in a ``ThreadPoolExecutor``.
- GraphQL (with *GraphiQL*) support!
- OpenAPI schema generation, with interactive documentation!
- Single-page webapp support!

Testimonials
------------

   “Pleasantly very taken with python-responder.
   `@kennethreitz`_ at his absolute best.”

   |

   — Rudraksh M.K.

..

   "ASGI is going to enable all sorts of new high-performance web services. It's awesome to see Responder starting to take advantage of that."

   |

   — Tom Christie, author of `Django REST Framework`_

..

   “I love that you are exploring new patterns. Go go go!”

   |

   — Danny Greenfield, author of `Two Scoops of Django`_


User Guides
-----------

.. toctree::
   :maxdepth: 2

   quickstart
   tour
   deployment
   testing
   api
   cli


Installing Responder
--------------------

Use ``uv`` for fast installation.

.. code-block:: shell

    uv pip install --upgrade 'responder'

Or use standard pip where ``uv`` is not available.

.. code-block:: shell

    pip install --upgrade 'responder'

Responder supports **Python 3.7+**. If you are looking at installing Responder
for hacking on it, please refer to the :ref:`sandbox` documentation.

.. toctree::
   :hidden:

   sandbox



The Basic Idea
--------------

The primary concept here is to bring the niceties that are brought forth from both Flask and Falcon and unify them into a single framework, along with some new ideas I have. I also wanted to take some of the API primitives that are instilled in the Requests library and put them into a web framework. So, you'll find a lot of parallels here with Requests.

- Setting ``resp.content`` sends back bytes.
- Setting ``resp.text`` sends back unicode, while setting ``resp.html`` sends back HTML.
- Setting ``resp.media`` sends back JSON/YAML (``.text``/``.html``/``.content`` override this).
- Case-insensitive ``req.headers`` dict (from Requests directly).
- ``resp.status_code``, ``req.method``, ``req.url``, and other familiar friends.

Ideas
-----

- Flask-style route expression, with new capabilities -- all while using Python 3.6+'s new f-string syntax.
- I love Falcon's "every request and response is passed into each view and mutated" methodology, especially ``response.media``, and have used it here. In addition to supporting JSON, I have decided to support YAML as well, as Kubernetes is slowly taking over the world, and it uses YAML for all the things. Content-negotiation and all that.
- **A built in testing client that uses the actual Requests you know and love**.
- The ability to mount other WSGI apps easily.
- Automatic gzipped-responses.
- In addition to Falcon's ``on_get``, ``on_post``, etc methods, Responder features an ``on_request`` method, which gets called on every type of request, much like Requests.
- A production static files server is built-in.
- `uvicorn`_ is built-in as a production web server. I would have chosen Gunicorn, but it doesn't run on Windows. Plus, uvicorn serves well to protect against `Slowloris`_ attacks, making Nginx unnecessary in production.
- GraphQL support, via Graphene. The goal here is to have any GraphQL query exposable at any route, magically.


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`


.. _@kennethreitz: https://x.com/kennethreitz
.. _ASGI: https://en.wikipedia.org/wiki/Asynchronous_Server_Gateway_Interface
.. _Django REST Framework: https://www.django-rest-framework.org/
.. _f-string syntax: https://docs.python.org/3/whatsnew/3.6.html#pep-498-formatted-string-literals
.. _Jinja: https://jinja.palletsprojects.com/en/stable/
.. _ServeStatic: https://archmonger.github.io/ServeStatic/latest/
.. _Slowloris: https://en.wikipedia.org/wiki/Slowloris_(computer_security)
.. _Starlette: https://www.starlette.io/
.. _Responder: https://responder.kennethreitz.org/
.. _Two Scoops of Django: https://www.feldroy.com/two-scoops-press#two-scoops-of-django
.. _uvicorn: https://www.uvicorn.org/
.. _uvloop: https://uvloop.readthedocs.io/
