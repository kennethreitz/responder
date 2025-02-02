# Example HTTP service definition, using Responder.
# https://pypi.org/project/responder/
import responder

api = responder.API()


@api.route("/{greeting}")
async def greet_world(req, resp, *, greeting):
    resp.text = f"{greeting}, world!"


if __name__ == "__main__":
    api.run()
