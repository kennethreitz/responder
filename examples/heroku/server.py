import os
import responder

api = responder.API(enable_hsts=True)


@api.route("/")
def route(req, resp):
    resp.text = "hello, world!"


api.run(port=int(os.environ["PORT"]))
