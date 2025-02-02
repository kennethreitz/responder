# Example HTTP service definition, using Responder.
# https://pypi.org/project/responder/
import responder

api = responder.API()


@api.route("/")
async def index(req, resp):
    resp.text = "Welcome"


@api.route("/user")
async def user_create(req, resp):
    data = await req.media()
    resp.text = f"Hello, {data['username']}"


@api.route("/user/{identifier}")
async def user_get(req, resp, *, identifier):
    resp.text = f"Hello, user {identifier}"


if __name__ == "__main__":
    api.run()
