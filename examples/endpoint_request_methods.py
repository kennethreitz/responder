import responder

api = responder.API()


@api.route("/{greeting}")
async def greet(req, resp, *, greeting):  # defaults to get.
    resp.text = f"GET - {greeting}, world!"


@api.route("/me/{greeting}", methods=["POST"])
async def greet_me(req, resp, *, greeting):
    resp.text = f"POST - {greeting}, world!"


@api.route("/class/{greeting}")
class GreetingResource:
    def on_get(self, req, resp, *, greeting):
        resp.text = f"GET class - {greeting}, world!"
        resp.headers.update({"X-Life": "42"})
        resp.status_code = api.status_codes.HTTP_416

    def on_post(self, req, resp, *, greeting):
        resp.text = f"POST class - {greeting}, world!"
        resp.headers.update({"X-Life": "42"})
        resp.status_code = api.status_codes.HTTP_416

    def on_request(self, req, resp, *, greeting):  # any request method.
        resp.text = f"any class - {greeting}, world!"
        resp.headers.update({"X-Life": "42"})
        resp.status_code = api.status_codes.HTTP_416


if __name__ == "__main__":
    api.run()
