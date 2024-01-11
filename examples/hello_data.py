from pydantic import BaseModel

import responder


class ItemModel(BaseModel):
    hello: str


api = responder.API()


@api.route("/upload")
async def receive_incoming(req, resp):
    data = await req.validate(ItemModel)
    resp.media = data.model_dump()


# Let's make an HTTP request to the server, to test it out.'}
r = api.requests.post("http://;/upload", data='{"hello": "world"}')
print(r.text)
