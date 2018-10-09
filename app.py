import responder
import graphene

api = responder.API()
# api.mount('/subapp', other_wsgi_app)


@api.route("/")
def hello(req, resp):
    print(resp)
    # resp.status = responder.status.ok
    resp.media = {"hello": "world"}


class ThingsResource:
    def on_request(self, req, resp):
        resp.status = responder.status.HTTP_200
        resp.media = ["ylolo"]


class Query(graphene.ObjectType):
    hello = graphene.String(name=graphene.String(default_value="stranger"))

    def resolve_hello(self, info, name):
        return "Hello " + name


schema = graphene.Schema(query=Query)


class GraphQLResource(responder.GraphQLSchema):
    import graphene

    def on_request(self, req, resp):
        resp.status = responder.status.HTTP_200
        print(schema.execute("{ hello }").data)

        resp.media = ["yolo"]


# Alerntatively,
api.add_route("/2", GraphQLResource)
api.add_route("/graph", schema)

print(
    api.session()
    .get(
        "http://app/graph?query={ hello }",
        headers={"Accept": "application/x-yaml"},
        # data="hello",
    )
    .text
)
