import time

from marshmallow import Schema, fields
from pydantic import BaseModel

import responder

api = responder.API()


class BookSchema(BaseModel):  # Pydantic schema
    price: float
    title: str


class HeaderSchema(Schema):  # Mashmellow schema
    x_version = fields.String(data_key="X-Version", required=True)


class CookiesSchema(BaseModel):
    max_age: int
    is_cheap: bool


class QuerySchema(Schema):
    page = fields.Int(missing=1)
    limit = fields.Int(missing=10)


# Media routes
@api.route("/book")
@api.media(BookSchema)
async def book_create(req, resp, *, data):
    @api.background.task
    def process_book(book):
        time.sleep(2)
        print(book)

    process_book(data)
    resp.media = {"msg": "created"}


# Query(params) route
@api.route("/books")
@api.arguments(QuerySchema)  # default location is `query`
async def pydantic_query(req, resp, *, query):
    print(query)  # e.g {'page': 2, 'limit': 20}
    resp.media = {"books": [{"title": "Python", "price": 39.00}]}


# Headers routes
@api.route("/book/{id}")
@api.arguments(HeaderSchema, location="headers")
async def pydantic_headers(req, resp, *, id, headers):
    """Header Pydantic"""
    print(headers)  # e.g {"x_version": "2.4.5"}
    resp.media = {"title": f"Lost {id}", "price": 9.99}


# Cookies routes
@api.route("/")
@api.arguments(CookiesSchema, location="cookies")
async def pydantic_cookies(req, resp, *, cookies):
    print(cookies)  # e.g {"max_age": "123", "is_cheap": True}
    resp.text = "Welcome to book store."


# Demonstrating stacking of decorators routes
@api.route("/book_stack")
@api.arguments(
    CookiesSchema, location="cookies", key="c"
)  # default key is the value of location.
@api.media(BookSchema)
async def pydantic_stack(req, resp, *, c, data):
    print(f"Cookies: {c}")
    resp.media = data


# Let's make an HTTP request to the server, to test it out.'}

# Media requests
r = api.requests.post("http://;/book", json={"price": 9.99, "title": "Pydantic book"})
print(r.json())


# Query(params) requests
r = api.requests.get("http://;/books?page=2&limit=20")
print(r.json())


# Headers requests
r = api.requests.post("http://;/book/1", headers={"X-Version": "2.4.5"})
print(r.json())


# Cookies requests
r = api.requests.post("http://;/", cookies={"max_age": "123", "is_cheap": "True"})
print(r.text)


r = api.requests.post(
    "http://;/book_stack",
    json={"price": 9.99, "title": "Pydantic book"},
    cookies={"max_age": "123", "is_cheap": "True"},
)
print(r.json())
