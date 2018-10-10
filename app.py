import responder
import graphene


from flask import Flask

app = Flask(__name__)


@app.route("/")
def hello_world():
    return "Hello, World from flask!"


api = responder.API()
api.mount("/hello", app)


@api.route("/")
def hello(req, resp):
    # resp.status = responder.status.ok
    resp.content = api.template("test.html")


class ThingsResource:
    def on_request(self, req, resp):
        resp.status = responder.status.HTTP_200
        resp.media = ["yolo"]


class Query(graphene.ObjectType):
    hello = graphene.String(name=graphene.String(default_value="stranger"))

    def resolve_hello(self, info, name):
        return "Hello " + name


schema = graphene.Schema(query=Query)

# Alerntatively,
api.add_route("/graph", schema, graphiql=True)


print(
    api.session()
    .get(
        "http://app/",
        # data="{ hello }",
        # headers={"Accept": "application/x-yaml"},
        # data="hello",
    )
    .headers
)

# print(
#     api.session()
#     .get(
#         "http://app/hello/",
#         data="{ hello }",
#         headers={"Accept": "application/x-yaml"},
#         # data="hello",
#     )
#     .text
# )
# {hello: Hello stranger}

# api.run(port=5000, expose_tracebacks=True)
