Responder
=========

A familiar HTTP Service Framework for Python.

.. code:: python

   import responder

   api = responder.API()

   @api.route("/{greeting}")
   async def greet_world(req, resp, *, greeting):
       resp.text = f"{greeting}, world!"

   if __name__ == '__main__':
       api.run()

Powered by `Starlette`_, `uvicorn`_, and good intentions. The ``async`` is optional.


The Idea
--------

If you've ever used `Flask`_, the routing will look familiar. If you've
used `Falcon`_, the request/response pattern will click immediately. And
if you've used `Requests`_ — well, you'll feel right at home.

Responder takes these ideas and brings them together. Every view receives
a request and a response. You read from one and write to the other. No
return values, no special response classes, no boilerplate.

- ``resp.text`` sends text. ``resp.html`` sends HTML. ``resp.media`` sends JSON.
- ``resp.file("path")`` serves a file. ``resp.content`` sends raw bytes.
- ``req.headers`` is case-insensitive. ``req.params`` holds query parameters.
- ``resp.status_code``, ``req.method``, ``req.url`` — the familiar ones.

Set ``resp.media`` to a dict and the right thing happens. If the client
asks for YAML, it gets YAML. Content negotiation is automatic.

Responder and `FastAPI`_ are siblings — both built on Starlette, both
born around the same time, both part of the push that made ASGI the
future of Python web services. FastAPI went deep on type annotations
and automatic validation. Responder went for simplicity and a mutable
request/response pattern. Both projects are better for the other
existing. Use whichever feels right.

This is a passion project. It exists because building a web framework
from scratch is one of the best ways to understand how the web works.
It's a great fit for personal projects, prototyping, teaching, research,
and anyone who values a clean API over a sprawling ecosystem. If you
need battle-tested infrastructure at scale, FastAPI and Django will
serve you well. If you want something small, expressive, and fun to
work with — welcome.


What You Get
------------

One ``pip install``, batteries included:

- Pydantic request validation and response serialization.
- Mount Flask, Django, or any WSGI/ASGI app at a subroute.
- Gzip compression, HSTS, CORS, and trusted host validation.
- Before-request and after-request hooks for auth and logging.
- A test client for fast, in-process testing with pytest.
- Route parameters with f-string syntax and type convertors.
- Lifespan context managers for startup and shutdown logic.
- Custom exception handlers for clean error responses.
- `GraphQL`_ with Graphene and a built-in GraphiQL IDE.
- Server-Sent Events for real-time streaming.
- File serving with automatic content-type detection.
- Sync and async views — ``async`` is always optional.
- Class-based views with ``on_get``, ``on_post``, ``on_request``.
- Built-in rate limiting with ``X-RateLimit`` headers.
- Content negotiation: JSON, YAML, and MessagePack.
- A pleasant API with a single import statement.
- OpenAPI schema generation with Swagger UI.
- A production `uvicorn`_ server, ready to deploy.
- Route groups for API versioning.
- Signed cookie-based sessions.
- Background tasks in a thread pool.
- WebSocket support.


Installation
------------

.. code-block:: shell

    $ uv pip install responder

Python 3.10 and above. That's it.


.. toctree::
   :maxdepth: 2
   :caption: User Guide

   quickstart
   tour
   deployment
   testing
   api
   cli

.. toctree::
   :maxdepth: 2
   :caption: Tutorials

   tutorial-rest
   tutorial-sqlalchemy
   tutorial-auth
   tutorial-websockets
   tutorial-middleware
   tutorial-flask
   guide-config

.. toctree::
   :maxdepth: 1
   :caption: Project

   changes
   Sandbox <sandbox>
   backlog


.. _Starlette: https://www.starlette.io/
.. _uvicorn: https://www.uvicorn.org/
.. _Flask: https://flask.palletsprojects.com/
.. _Falcon: https://falconframework.org/
.. _FastAPI: https://fastapi.tiangolo.com/
.. _GraphQL: https://graphql.org/
.. _Requests: https://requests.readthedocs.io/
