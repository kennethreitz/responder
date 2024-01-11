import time

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
    openapi_theme="elements",
)


@api.schema("BookCreate")
class BookCreateSchema(BaseModel):
    price: float
    title: str


# @api.schema("BookCreate")
# class BookCreateSchema(Schema):
#     price = fields.Float()
#     title = fields.Str()


@api.route("/book")
@api.trust(BookCreateSchema)
@api.ensure(BookCreateSchema)
@api.expect_when(
    {
        api.status_codes.HTTP_404: "Account not found",
        api.status_codes.HTTP_400: "Bad request",
    }
)
async def receive_incoming(req, resp, *, data):
    @api.background.task
    def process_book(book):
        time.sleep(2)
        print(book)

    data = await req.media()
    process_book(data)
    resp.media = {"msg": "created"}


# Let's make an HTTP request to the server, to test it out.'}
# r = api.requests.post("http://;/book", json={"price": 10.99, "title": "Monster Hunter"})
# print(r.text)

if __name__ == "__main__":
    api.run()
