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

Powered by `Starlette`_ and `uvicorn`_. The ``async`` is optional.


The Idea
--------

Responder takes the best ideas from `Flask`_ and `Falcon`_ and brings them
together into one clean framework.

The request and response objects are passed into every view and mutated
directly — no return values, no boilerplate. If you've used Requests,
you'll feel right at home. If you've used Flask, the routing will look
familiar. If you've used Falcon, the ``req`` / ``resp`` pattern will
click immediately.

- ``resp.text`` sends back text. ``resp.html`` sends back HTML.
- ``resp.media`` sends back JSON — or YAML, if the client asks for it.
- ``resp.file("path")`` serves a file. ``resp.content`` sends raw bytes.
- ``req.headers`` is case-insensitive. ``req.params`` holds query parameters.
- ``resp.status_code``, ``req.method``, ``req.url`` — the usual suspects.

Content negotiation happens automatically. Set ``resp.media`` to a dict
and Responder figures out the rest.

Responder and `FastAPI`_ share DNA — both are built on Starlette, both
appeared around the same time, and both pushed Python's ASGI ecosystem
forward. FastAPI went deep on type annotations and automatic validation.
Responder went for a mutable request/response pattern and a simpler,
more familiar API. Both projects are better for the other existing, and
you should use whichever feels right for what you're building.


What You Get
------------

One ``pip install``, batteries included:

- Mount Flask, Django, or any WSGI/ASGI app at a subroute.
- Gzip compression, HSTS, CORS, and trusted host validation.
- Before-request hooks that can short-circuit for auth guards.
- A test client for fast, in-process testing with pytest.
- Route parameters with f-string syntax and type convertors.
- Lifespan context managers for startup and shutdown logic.
- Custom exception handlers for clean error responses.
- `GraphQL`_ with Graphene and a built-in GraphiQL IDE.
- File serving with automatic content-type detection.
- Sync and async views — ``async`` is always optional.
- Class-based views with ``on_get``, ``on_post``, ``on_request``.
- A pleasant API with a single import statement.
- OpenAPI schema generation with Swagger UI.
- A production `uvicorn`_ server, ready to deploy.
- HTTP method filtering for REST APIs.
- Signed cookie-based sessions.
- Background tasks in a thread pool.
- WebSocket support.


Installation
------------

.. code-block:: shell

    $ uv pip install responder

Python 3.9 and above. That's it.


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
