import time
from marshmallow import Schema, fields
from pydantic import BaseModel
import responder


description = "This is a sample server for a pet store."
terms_of_service = "http://example.com/terms/"
contact = {
    "name": "API Support",
    "url": "http://www.example.com/support",
    "email": "support@example.com",
}
license = {
    "name": "Apache 2.0",
    "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
}

api = responder.API(
    title="Web Service",
    version="1.0",
    openapi="3.0.2",
    docs_route="/docs",
    description=description,
    terms_of_service=terms_of_service,
    contact=contact,
    license=license,
    api_theme="elements",
)


@api.schema("PydanticBookCreate")
class PydanticBookSchema(BaseModel):
    price: float
    title: str


@api.schema("BookCreate")
class MarshmallowBookSchema(Schema):
    price = fields.Float()
    title = fields.Str()


@api.route("/pybook")
@api.trust(PydanticBookSchema)
async def pydantic_create(req, resp, *, data):
    "Create Pydantic book"

    @api.background.task
    def process_book(book):
        time.sleep(2)
        print(book)

    process_book(data)
    resp.media = {"msg": "created"}


@api.route("/marshbook")
@api.trust(MarshmallowBookSchema)
async def marshmallow_create(req, resp, *, data):
    "Create create marshmallow book"

    @api.background.task
    def process_book(book):
        time.sleep(2)
        print(book)

    process_book(data)
    resp.media = {"msg": "created"}


# Let's make an HTTP request to the server, to test it out.'}
r = api.requests.post("http://;/pybook", json={"price": 9.99, "title": "Pydantic book"})
print(r.text)

r = api.requests.post(
    "http://;/marshbook", json={"price": 10.99, "title": "Marshmallow book"}
)
print(r.text)
