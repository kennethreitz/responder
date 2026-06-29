Generated Clients
=================

Responder can generate small API clients from the OpenAPI schema it already
builds from your routes, markers, and Pydantic models.

Enable OpenAPI on your app, then write a Python client module:

.. code-block:: python

    import responder

    api = responder.API(title="Service", version="1", openapi="3.0.2")

    @api.get("/users/{user_id}", operation_id="get_user")
    def get_user(req, resp, *, user_id: int):
        resp.media = {"id": user_id}

    api.generate_client("clients/service.py", class_name="ServiceClient")

Use ``language=`` to generate clients for other ecosystems:

.. code-block:: python

    api.generate_client(
        "clients/service.ts",
        class_name="ServiceClient",
        language="typescript",
    )
    api.generate_client("clients/service.js", language="javascript")
    api.generate_client("clients/service.rb", language="ruby")
    api.generate_client("clients/ServiceClient.php", language="php")

Supported languages are ``python``, ``javascript``, ``typescript``, ``ruby``,
and ``php``. The generated modules have no Responder runtime dependency. Python,
Ruby, and PHP clients use standard libraries for HTTP calls; JavaScript and
TypeScript clients use ``fetch``.

.. code-block:: python

    from clients.service import ServiceClient

    client = ServiceClient("https://api.example.com", bearer_token="secret")
    user = client.get_user(42)

For Python tests, pass Responder's in-process test client. This lets your
generated client exercise the app without a listening socket:

.. code-block:: python

    client = ServiceClient(session=api.requests)
    assert client.get_user(42) == {"id": 42}

``API.generate_client(...)`` returns source code when no path is supplied:

.. code-block:: python

    source = api.generate_client(class_name="ServiceClient")
    typescript = api.generate_client(
        class_name="ServiceClient",
        language="typescript",
    )

The lower-level helpers are available from :mod:`responder.ext.clientgen`:

.. code-block:: python

    from responder.ext.clientgen import generate_client, write_client

    source = generate_client(api.openapi, class_name="ServiceClient")
    write_client(api.openapi, "clients/service.py", class_name="ServiceClient")
    write_client(
        api.openapi,
        "clients/service.ts",
        class_name="ServiceClient",
        language="typescript",
    )

Generated clients include:

- method signatures generated from path and query parameters,
- JSON request-body support,
- bearer, basic, and API-key header helpers,
- structured ``APIError`` exceptions for non-2xx responses,
- real HTTP transport for network calls,
- typed Python and TypeScript signatures where OpenAPI exposes enough schema,
- a Python-only ``session=`` hook for Starlette/httpx-style clients in tests.
