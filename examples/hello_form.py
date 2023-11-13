from typing import List, Optional

import responder
from pydantic import BaseModel


api = responder.API()

# responder.Model
class Item(BaseModel):
    hello: List

@api.route("/file")
async def receive_incoming(req, resp):

    # Parse the incoming data as form-encoded.
    # Note: 'json' and 'yaml' formats are also automatically supported.
    data = await req.data(Item)
    
    # Print the incoming data to stdout.
    print(data)

    # Immediately respond that upload was successful.
    resp.media = {'success': True}


# Let's make an HTTP request to the server, to test it out.'}
r = api.requests.post('http://;/file', data={'hello': 'world'})
print(r.text)
