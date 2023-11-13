import responder
from pydantic import BaseModel


class ItemModel(BaseModel):
    hello2: list

api = responder.API()

@api.route("/file")
async def receive_incoming(req, resp):
    print(await req.validate(ItemModel))

# Let's make an HTTP request to the server, to test it out.'}
r = api.requests.post('http://;/file', data={'hello': 'world'})
print(r.text)
