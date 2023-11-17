import subprocess
import responder

api = responder.API()

@api.route("/fortune")
class GreetingResource:
    def on_request(self, req, resp):   # or on_get...
        resp.headers.update({'X-Life': '42'})

    def on_get(self, req, resp):
        resp.headers.update({'X-ArtificialLife': '400'})

        fortune = subprocess.check_output(["fortune"]).decode()
        resp.media = {"fortune": fortune}

# Let's make an HTTP request to the server, to test it out.'}
r = api.requests.get("http://;/fortune")
print(r.text)
print(r.headers)
