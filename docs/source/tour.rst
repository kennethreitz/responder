Feature Tour
============


Class-Based Views
-----------------

Class-based views (and setting some headers and stuff)::

    @api.route("/{greeting}")
    class GreetingResource:
        def on_request(self, req, resp, *, greeting):   # or on_get...
            resp.text = f"{greeting}, world!"
            resp.headers.update({'X-Life': '42'})
            resp.status_code = api.status_codes.HTTP_416


Background Tasks
----------------

Here, you can spawn off a background thread to run any function, out-of-request::

    @api.route("/")
    def hello(req, resp):

        @api.background.task
        def sleep(s=10):
            time.sleep(s)
            print("slept!")

        sleep()
        resp.content = "processing"


GraphQL
-------

Serve a GraphQL API::

    import graphene

    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))

        def resolve_hello(self, info, name):
            return f"Hello {name}"

    schema = graphene.Schema(query=Query)
    view = responder.ext.GraphQLView(api=api, schema=schema)

    api.add_route("/graph", view)

Visiting the endpoint will render a *GraphiQL* instance, in the browser.

You can make use of Responder's Request and Response objects in your GraphQL resolvers through ``info.context['request']`` and ``info.context['response']``.


OpenAPI Schema Support
----------------------

Responder comes with built-in support for OpenAPI / marshmallow

New in Responder `1.4.0`::

    import responder
    from responder.ext.schema import Schema as OpenAPISchema
    from marshmallow import Schema, fields

    contact = {
        "name": "API Support",
        "url": "http://www.example.com/support",
        "email": "support@example.com",
    }
    license = {
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    }
    
    api = responder.API()

    schema = OpenAPISchema(
        app=api,
        title="Web Service",
        version="1.0",
        openapi="3.0.2",
        description="A simple pet store",
        terms_of_service="http://example.com/terms/",
        contact=contact,
        license=license,
    )

    @schema.schema("Pet")
    class PetSchema(Schema):
        name = fields.Str()


    @api.route("/")
    def route(req, resp):
        """A cute furry animal endpoint.
        ---
        get:
            description: Get a random pet
            responses:
                200:
                    description: A pet to be returned
                    content:  
                        application/json: 
                            schema: 
                                $ref: '#/components/schemas/Pet'                         
        """
        resp.media = PetSchema().dump({"name": "little orange"})


Old way *It's recommended to use the code above* ::

    import responder
    from marshmallow import Schema, fields

    contact = {
        "name": "API Support",
        "url": "http://www.example.com/support",
        "email": "support@example.com",
    }
    license = {
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    }

    api = responder.API(
        title="Web Service",
        version="1.0",
        openapi="3.0.2",
        description="A simple pet store",
        terms_of_service="http://example.com/terms/",
        contact=contact,
        license=license,
    )

    @api.schema("Pet")
    class PetSchema(Schema):
        name = fields.Str()

    @api.route("/")
    def route(req, resp):
        """A cute furry animal endpoint.
        ---
        get:
            description: Get a random pet
            responses:
                200:
                    description: A pet to be returned
                    content:  
                        application/json: 
                            schema: 
                                $ref: '#/components/schemas/Pet'                         
        """
        resp.media = PetSchema().dump({"name": "little orange"})

::

    >>> r = api.session().get("http://;/schema.yml")

    >>> print(r.text)
    components:
      parameters: {}
      responses: {}
      schemas:
        Pet:
          properties:
            name: {type: string}
          type: object
      securitySchemes: {}
    info:
      contact: {email: support@example.com, name: API Support, url: 'http://www.example.com/support'}
      description: This is a sample server for a pet store.
      license: {name: Apache 2.0, url: 'https://www.apache.org/licenses/LICENSE-2.0.html'}
      termsOfService: http://example.com/terms/
      title: Web Service
      version: 1.0
    openapi: 3.0.2
    paths:
      /:
        get:
          description: Get a random pet
          responses:
            200: {description: A pet to be returned, schema: $ref: "#/components/schemas/Pet"}
    tags: []


Interactive Documentation
-------------------------

Responder can automatically supply API Documentation for you. Using the example above

The new and recommended way::

    ...
    from responder.ext.schema import Schema
    ...
    api = responder.API()

    schema = Schema(
        app=api,
        title="Web Service",
        version="1.0",
        openapi="3.0.2",
        ...
        docs_route='/docs',
        ...
        description=description,
        terms_of_service=terms_of_service,
        contact=contact,
        license=license,
    )
    

The old way ::

    api = responder.API(
        title="Web Service",
        version="1.0",
        openapi="3.0.2",
        docs_route='/docs',
        description=description,
        terms_of_service=terms_of_service,
        contact=contact,
        license=license,
    )

This will make ``/docs`` render interactive documentation for your API.

Mount a WSGI / ASGI Apps (e.g. Flask, Starlette,...)
----------------------------------------------------

Responder gives you the ability to mount another ASGI / WSGI app at a subroute::

    import responder
    from flask import Flask

    api = responder.API()
    flask = Flask(__name__)

    @flask.route('/')
    def hello():
        return 'hello'

    api.mount('/flask', flask)

That's it!

Single-Page Web Apps
--------------------

If you have a single-page webapp, you can tell Responder to serve up your ``static/index.html`` at a route, like so::

    api.add_route("/", static=True)

This will make ``index.html`` the default response to all undefined routes.

Reading / Writing Cookies
-------------------------

Responder makes it very easy to interact with cookies from a Request, or add some to a Response::

    >>> resp.cookies["hello"] = "world"

    >>> req.cookies
    {"hello": "world"}


To set cookies directives, you should use `resp.set_cookie`::

    >>> resp.set_cookie("hello", value="world", max_age=60)

Supported directives:

* ``key`` - **Required**
* ``value`` - [OPTIONAL] - Defaults to ``""``. 
* ``expires`` - Defaults to ``None``.
* ``max_age`` - Defaults to ``None``.
* ``domain`` - Defaults to ``None``.
* ``path`` - Defaults to ``"/"``.
* ``secure`` - Defaults to ``False``.
* ``httponly`` - Defaults to ``True``.

For more information see `directives <https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie#Directives>`_


Using Cookie-Based Sessions
---------------------------

Responder has built-in support for cookie-based sessions. To enable cookie-based sessions, simply add something to the ``resp.session`` dictionary::

    >>> resp.session['username'] = 'kennethreitz'

A cookie called ``Responder-Session`` will be set, which contains all the data in ``resp.session``. It is signed, for verification purposes.

You can easily read a Request's session data, that can be trusted to have originated from the API::

    >>> req.session
    {'username': 'kennethreitz'}

**Note**: if you are using this in production, you should pass the ``secret_key`` argument to ``API(...)``::

    api = responder.API(secret_key=os.environ['SECRET_KEY'])

Using ``before_request``
------------------------

If you'd like a view to be executed before every request, simply do the following::

    @api.route(before_request=True)
    def prepare_response(req, resp):
        resp.headers["X-Pizza"] = "42"

Now all requests to your HTTP Service will include an ``X-Pizza`` header.

For ``websockets``::

    @api.route(before_request=True, websocket=True)
    def prepare_response(ws):
        await ws.accept()


WebSocket Support
-----------------

Responder supports WebSockets::

    @api.route('/ws', websocket=True)
    async def websocket(ws):
        await ws.accept()
        while True:
            name = await ws.receive_text()
            await ws.send_text(f"Hello {name}!")
        await ws.close()

Accepting the connection::

    await websocket.accept()

Sending and receiving data::

    await websocket.send_{format}(data) 
    await websocket.receive_{format}(data)

Supported formats: ``text``, ``json``, ``bytes``.

Closing the connection::

    await websocket.close()

Using Requests Test Client
--------------------------

Responder comes with a first-class, well supported test client for your ASGI web services: **Requests**.

Here's an example of a test (written with pytest)::

    import myapi

    @pytest.fixture
    def api():
        return myapi.api

    def test_response(api):
        hello = "hello, world!"

        @api.route('/some-url')
        def some_view(req, resp):
            resp.text = hello

        r = api.requests.get(url=api.url_for(some_view))
        assert r.text == hello

HSTS (Redirect to HTTPS)
------------------------

Want HSTS (to redirect all traffic to HTTPS)?

::

    api = responder.API(enable_hsts=True)


Boom.

CORS
----

Want `CORS <https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS/>`_ ?

::

    api = responder.API(cors=True)


The default parameters used by **Responder** are restrictive by default, so you'll need to explicitly enable particular origins, methods, or headers, in order for browsers to be permitted to use them in a Cross-Domain context.

In order to set custom parameters, you need to set the ``cors_params`` argument of ``api``, a dictionary containing the following entries:

* ``allow_origins`` - A list of origins that should be permitted to make cross-origin requests. eg. ``['https://example.org', 'https://www.example.org']``. You can use ``['*']`` to allow any origin.
* ``allow_origin_regex`` - A regex string to match against origins that should be permitted to make cross-origin requests. eg. ``'https://.*\.example\.org'``.
* ``allow_methods`` - A list of HTTP methods that should be allowed for cross-origin requests. Defaults to `['GET']`. You can use ``['*']`` to allow all standard methods.
* ``allow_headers`` - A list of HTTP request headers that should be supported for cross-origin requests. Defaults to ``[]``. You can use ``['*']`` to allow all headers. The ``Accept``, ``Accept-Language``, ``Content-Language`` and ``Content-Type`` headers are always allowed for CORS requests.
* ``allow_credentials`` - Indicate that cookies should be supported for cross-origin requests. Defaults to ``False``.
* ``expose_headers`` - Indicate any response headers that should be made accessible to the browser. Defaults to ``[]``.
* ``max_age`` - Sets a maximum time in seconds for browsers to cache CORS responses. Defaults to ``60``.

Trusted Hosts
-------------

Make sure that all the incoming requests headers have a valid ``host``, that matches one of the provided patterns in the ``allowed_hosts`` attribute, in order to prevent HTTP Host Header attacks.

A 400 response will be raised, if a request does not match any of the provided patterns in the ``allowed_hosts`` attribute.

::

    api = responder.API(allowed_hosts=['example.com', 'tenant.example.com'])

* ``allowed_hosts`` - A list of allowed hostnames. 

Note:

* By default, all hostnames are allowed.
* Wildcard domains such as ``*.example.com`` are supported.
* To allow any hostname use ``allowed_hosts=["*"]``.
