Feature Tour
============


Class-Based Views
-----------------

Class-based views (and setting some headers and stuff)::

    @api.route("/{greeting}")
    class GreetingResource:
        def on_request(req, resp, *, greeting):   # or on_get...
            resp.text = f"{greeting}, world!"
            resp.headers.update({'X-Life': '42'})
            resp.status_code = api.status_codes.HTTP_416

Template Rendering
------------------
Render a template, with arguments::


    @api.route("/{greeting}")
    def greet_world(req, resp, *, greeting):
        resp.content = api.template("index.html", greeting=greeting)


The ``api`` instance is available as an object during template rendering.

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
            return "Hello " + name

    api.add_route("/graph", graphene.Schema(query=Query))


Built-in Testing Client (Requests)
----------------------------------

We can then send a query to our service::

    >>> requests = api.session()
    >>> r = requests.get("http://;/graph", params={"query": "{ hello }"})
    >>> r.json()
    {'data': {'hello': 'Hello stranger'}}


Or, request YAML back::

    >>> r = requests.get("http://;/graph", params={"query": "{ hello(name:\"john\") }"}, headers={"Accept": "application/x-yaml"})
    >>> print(r.text)
    data: {hello: Hello john}


HSTS (Redirect to HTTPS)
------------------------

Want HSTS?

::

    api = responder.API(enable_hsts=True)


Boom.
