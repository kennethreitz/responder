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

When your OpenAPI schema includes component models, Python and TypeScript
clients also generate model types. Request bodies use the request model type,
and methods return the documented success response type:

.. code-block:: python

    class ItemIn(TypedDict):
        name: str

    class ItemOut(TypedDict):
        id: int
        name: str

    def create_item(self, body: ItemIn | None = None) -> ItemOut:
        ...

.. code-block:: typescript

    export interface ItemIn {
      name: string;
    }

    export interface ItemOut {
      id: number;
      name: string;
    }

    create_item(body: ItemIn | null = null): Promise<ItemOut>

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

The command-line interface can generate clients from an import target too:

.. code-block:: shell

    responder client app:api > clients/service.py
    responder client --lang typescript --class-name ServiceClient \
        --output clients/service.ts app:api

Generated clients include:

- method signatures generated from path and query parameters,
- JSON request-body support,
- bearer, basic, and API-key header helpers,
- structured ``APIError`` exceptions for non-2xx responses,
- real HTTP transport for network calls,
- Python ``TypedDict`` models and TypeScript interfaces for OpenAPI components,
- typed Python and TypeScript parameters/returns where schemas are available,
- a Python-only ``session=`` hook for Starlette/httpx-style clients in tests.
