import responder
import graphene


from flask import Flask

app = Flask(__name__)


@app.route("/")
def hello_world():
    return "Hello, World from flask!"


api = responder.API(enable_hsts=False)
api.mount("/hello", app)

import time


@api.route("/")
def hello(req, resp):
    # resp.status = responder.status.ok

    @api.background.task
    def sleep(s=10):
        time.sleep(s)
        print("slept!")

    sleep()
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
api.add_route("/graph", schema)

print(
    api.session()
    .get(
        "http://app/",
        # data="{ hello }",
        # headers={"Accept": "application/x-yaml"},
        # data="hello",
    )
    .text
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

api.run(port=5000)
