import time
import responder

from pydantic import BaseModel

api = responder.API()

# responder.Model
class Item(BaseModel):
    # file: dict
    hello: list

@api.route("/file")
async def receive_incoming(req, resp):

    @api.background.task
    def process_data(data):
        """Just sleeps for three seconds, as a demo."""
        time.sleep(3)


    # Parse the incoming data as form-encoded.
    # Note: 'json' and 'yaml' formats are also automatically supported.
    data = await req.data(Item)
    print(data)

    # Process the data (in the background).
    process_data(data)

    # Immediately respond that upload was successful.
    resp.media = {'success': True}


# Let's make an HTTP request to the server, to test it out.'}
r = api.requests.post('http://;/file', data={'hello': 'world'})

print(r.text)
