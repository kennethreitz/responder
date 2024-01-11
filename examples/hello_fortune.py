import subprocess

from pydantic import BaseModel

import responder

api = responder.API()


class FortuneModel(BaseModel):
    fortune: str


def generate_fortune():
    fortune = subprocess.check_output(["fortune"]).decode().strip()
    return FortuneModel(fortune=fortune)


@api.ensure(FortuneModel)
@api.route("/fortune")
class GreetingResource:
    def on_get(self, req, resp):
        fortune = generate_fortune()
        resp.media = {"fortune": fortune.fortune}


# Let's make an HTTP request to the server, to test it out.'}
r = api.requests.get("http://;/fortune")
print(r.text)
