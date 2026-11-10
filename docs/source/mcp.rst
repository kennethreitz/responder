Model Context Protocol
======================

Responder can publish your routes as
`Model Context Protocol <https://modelcontextprotocol.io/>`_ tools. An MCP
client lists the tools, then calls one by name. The call is an ordinary
request inside the process, so the route you wrote is the tool you expose.

The endpoint is off until you turn it on::

    import responder

    api = responder.API(mcp=True)

``mcp=True`` mounts Streamable HTTP at ``/mcp``. A path string mounts it
somewhere else, and :meth:`responder.API.mcp` is the same switch when you
want to be explicit::

    api = responder.API()
    api.mcp("/agents")


What becomes a tool
-------------------

Every eligible HTTP route is a tool. WebSocket routes, typed SSE and NDJSON
streams, the docs and schema pages, and routes that require a file upload
are left out. A file upload has no JSON shape a tool call can send.

Opt a route out with ``mcp=False``. To publish only the routes you mark,
open the endpoint in explicit mode and set ``mcp=True`` on each one::

    api.mcp(include="explicit")

    @api.get("/search", mcp=True)
    def search(req, resp, *, q: str):
        """Search the catalog."""
        resp.media = {"q": q}

    @api.get("/health")
    def health(req, resp):
        resp.text = "ok"

``search`` is a tool. ``health`` is not. ``mcp=`` is per registration, so
the same function can be public on one path and hidden on another.

The tool name is the ``operation_id`` when you set one, otherwise the
function name (or ``Class_get`` for a class-based view). A second route
with the same name gets the method appended (``create_item_post``). The
description is the route's ``description=``, then its docstring, then its
``summary=``. ``summary=`` is also the tool title when a longer description
exists.

The input schema is JSON Schema built from the same pieces as OpenAPI:

- path parameters, including ``{id:int}`` and a ``Path()`` marker
- ``Query()``, ``Header()``, and ``Cookie()`` markers
- a ``params_model``
- a Pydantic body model, flattened into the tool's arguments
- form fields, when the handler has no JSON body

A name that appears both in the path and in the body is nested under the
body parameter instead of being flattened. ``Header(alias="X-Token")`` is
published under ``X-Token``, which is the header the call sends.


Calling a tool
--------------

``tools/call`` builds the request the route expects and runs it through the
application. Middleware, sessions, CSRF, before-request hooks, auth, and
dependencies all apply. Credentials on the MCP request are forwarded:
``Authorization``, cookies, and any other header you sent, apart from
framing headers such as ``Content-Type`` and ``Accept``. Declare a header
the tool should set itself with ``Header()``.

A response below status 400 comes back as the tool result. JSON objects are
also returned as ``structuredContent``. A Pydantic object response model is
published as ``outputSchema``. Status 400 and above come back with
``isError`` set, and the text starts with the HTTP status, so a validation
failure is still the same 422 the route would return to curl.

The MCP route itself does not require a CSRF token. Unsafe routes it calls
still do. Send the token on the MCP request (``X-CSRF-Token``, or the
session cookie plus the header) and it is forwarded with the tool call.


Wire protocol
-------------

The mount is one Streamable HTTP endpoint.

- ``POST`` a single JSON-RPC 2.0 object. The response is one JSON object
  when the client accepts ``application/json`` (the usual ``Accept`` of
  ``application/json, text/event-stream`` does). A client that accepts only
  ``text/event-stream`` gets one SSE ``message`` frame instead.
- ``notifications/initialized`` and any other ``notifications/*`` message
  get ``202`` and an empty body.
- ``GET`` and ``DELETE`` answer ``405``. This server does not open a
  server-initiated event stream and does not assign a session id.
- A browser ``Origin`` that does not match the request ``Host`` is rejected
  with ``403``. Clients that send no ``Origin`` are accepted.
- Supported protocol revisions are ``2024-11-05``, ``2025-03-26``,
  ``2025-06-18``, and ``2025-11-25``. ``initialize`` echoes a supported
  revision and otherwise answers with the newest one.

A short session looks like this::

    POST /mcp
    Accept: application/json, text/event-stream
    Content-Type: application/json

    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "demo", "version": "0"}}}

Then list and call::

    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}

    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "get_item", "arguments": {"item_id": 3}}}

``ping`` answers ``{}``. There is nothing to install. The official ``mcp``
package can host Streamable HTTP too, but its ASGI app expects the host
process to enter a session-manager lifespan, and a mounted sub-application's
lifespan does not run. Responder speaks this surface directly so the mount
stays a one-liner and the test client can call it in-process.

Pass ``name=`` and ``instructions=`` to :meth:`responder.API.mcp` when the
defaults (the OpenAPI title, or ``"Responder"``, and a one-line description
of the pipeline) are not what you want clients to see.
