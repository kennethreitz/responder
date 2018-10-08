import responder

api = responder.API(yaml_allowed=True)
# api.mount('/subapp', other_wsgi_app)


@api.route("/")
def hello(req, resp):
    resp.status = responder.http_status.ok
    resp.media = {"hello": "world"}
    resp.text = ""
    resp.content = ""


class ThingsResource:
    def on_request(self, req, resp):
        resp.status = responder.status.HTTP_200


# Alerntatively,
api.add_route("/{hello}", ThingsResource)
print(api.routes)
