from typing import List, Optional

import responder
from pydantic import BaseModel


api = responder.API()

# responder.Model
class ItemModel(BaseModel):
    hello2: List

@api.route("/file")
async def receive_incoming(req, resp):
    print(await req.validate(ItemModel))


# Let's make an HTTP request to the server, to test it out.'}
r = api.requests.post('http://;/file', data={'hello': 'world'})
print(r.text)
