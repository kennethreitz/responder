Examples
========

The ``examples/`` directory is the quickest way to see Responder's pieces
working together. Each file is a small app you can run directly, poke with
``curl``, and copy from when you need the same pattern in your own service.


Running an Example
------------------

From an installed environment, run an example with the Responder CLI::

    $ responder run examples/helloworld.py

Then call it from another terminal::

    $ curl http://127.0.0.1:5042/hello
    hello, world!

When you are working from a checkout of this repository, prefix the command
with ``uv run`` so the project environment is used::

    $ uv run responder run examples/rest_api.py

Most examples expose interactive OpenAPI docs at ``/docs``. Open
``http://127.0.0.1:5042/docs`` in a browser after starting one of the API
examples.


Where to Start
--------------

``helloworld.py``
    The smallest useful app: one index route, one route parameter, and no
    sessions. Start here if you want to see the request/response shape.

``rest_api.py``
    A compact book catalog with Pydantic input models, response models,
    OpenAPI metadata, ``resp.created()``, ``resp.no_content()``, and
    ``resp.problem()``.

``todo.py``
    The practical all-around example: typed input, filtering, bearer-token
    auth, named auth policies, request IDs, and richer OpenAPI examples.

``atelier.py``
    The contract-test fixture. It is intentionally polished because the test
    suite validates its generated OpenAPI document and drives a generated
    client against it.


Focused Patterns
----------------

``user.py``
    A smaller typed CRUD-style API for users.

``shortlinks.py``
    Redirects, conflict responses, generated short codes, and click tracking.

``webhooks.py``
    Raw-body signature verification before JSON validation, useful for
    Stripe-style webhook receivers.

``fortunes.py``
    A local-command wrapper that turns the ``fortune`` CLI into an API and
    reports command failures as problem-details responses. Install the
    ``fortune`` command, or set ``FORTUNE_COMMAND`` to a compatible command.

``lifespan.py``
    Startup and shutdown work with an async lifespan context manager.

``sse_stream.py``
    Server-Sent Events with heartbeat support and a tiny browser page.

``websocket_chat.py``
    A minimal WebSocket chat room with a shared in-memory hub.

``marimo_mount.py``
    Mounts a marimo notebook under ``/notebooks/`` while keeping normal
    Responder routes and OpenAPI docs on the parent app. Install marimo first::

        $ uv pip install marimo

``tarot.py``
    A larger read/browse/deal API with Pydantic models and repeatable example
    data.


Testing Examples
----------------

Several examples expose a ``create_api()`` function so tests can build a fresh
app with a fake store or fake external dependency. For example,
``examples/fortunes.py`` accepts a ``fortune_reader`` callable, which lets the
test suite verify the API contract without depending on the local
``fortune`` binary.

That shape is a good habit for your own apps too: keep route wiring in
``create_api()``, pass real dependencies in production, and pass fakes in
tests.
