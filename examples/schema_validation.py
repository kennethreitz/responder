import time

import yaml
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
    openapi_theme="elements",
)


@api.schema("PydanticBookSchema")
class PydanticBookSchema(BaseModel):
    price: float
    title: str


@api.schema("MarshmallowBookSchema")
class MarshmallowBookSchema(Schema):
    price = fields.Float()
    title = fields.Str()


@api.schema("PydanticHeaderSchema")
class PydanticHeaderSchema(BaseModel):
    x_version: str


@api.schema("MarshmallowHeaderSchema")
class MarshmallowHeaderSchema(Schema):
    x_version = fields.String(data_key="X-Version", required=True)


@api.schema("PydanticCookiesSchema")
class PydanticCookiesSchema(BaseModel):
    max_age: int
    is_cheap: bool


@api.schema("MarshmallowCookiesSchema")
class MarshmallowCookiesSchema(Schema):
    max_age = fields.Int()
    is_cheap = fields.Bool()


# Media routes
@api.route("/pybook")
@api.media(PydanticBookSchema)
async def pydantic_create(req, resp, *, data):
    """Create Pydantic book"""

    @api.background.task
    def process_book(book):
        time.sleep(2)
        print(book)

    process_book(data)
    resp.media = {"msg": "Py created"}


@api.route("/marshbook")
@api.media(MarshmallowBookSchema)
async def marshmallow_create(req, resp, *, data):
    "Create create marshmallow book"

    @api.background.task
    def process_book(book):
        time.sleep(2)
        print(book)

    process_book(data)
    resp.media = {"msg": "Ma created"}


# Query(params) routes
@api.route("/py_book_query")
@api.arguments(PydanticBookSchema)
async def pydantic_query(req, resp, *, query):
    """Query Pydantic book"""
    print(query)
    resp.text = "PyBook found from query"


@api.route("/marsh_book_query")
@api.arguments(MarshmallowBookSchema, location="params")
async def marshmallow_query(req, resp, *, params):
    """Query create marshmallow book"""

    print(params)
    resp.text = "MaBook found from query"


# Headers routes
@api.route("/py_book_headers")
@api.arguments(PydanticHeaderSchema, location="headers")
async def pydantic_headers(req, resp, *, headers):
    """Header Pydantic"""
    print(headers)
    resp.text = "Pydantic headers verified"


@api.route("/marsh_book_headers")
@api.arguments(MarshmallowHeaderSchema, location="headers")
async def marshmallow_headers(req, resp, *, headers):
    """Headers marshmallow"""

    print(headers)
    resp.text = "Marshmallow headers verified"


# Cookies routes
@api.route("/py_book_cookies")
@api.arguments(PydanticCookiesSchema, location="cookies")
async def pydantic_cookies(req, resp, *, cookies):
    """Cookies Pydantic"""
    print(cookies)
    resp.text = "Pydantic cookies verified"


@api.route("/marsh_book_cookies")
@api.arguments(MarshmallowCookiesSchema, location="cookies")
async def marshmallow_cookies(req, resp, *, cookies):
    """Cookies marshmallow"""

    print(cookies)
    resp.text = "Marshmallow cookies verified"


# Stackind decorators routes
@api.route("/pystack")
@api.arguments(PydanticBookSchema, key="q")
@api.media(PydanticBookSchema)
async def pydantic_stack(req, resp, *, q, data):
    """Create Pydantic book"""

    print(f"Query: {q}")
    print(f"Data: {data}")

    resp.text = "Stacked Pydantic"


@api.route("/mashstack")
@api.arguments(MarshmallowCookiesSchema, location="cookies")
@api.media(MarshmallowBookSchema)
@api.expect(
    {
        401: "Invalid access or refresh token",
        403: "Please verify your account",
    }
)
async def marshmallow_stacks(req, resp, *, cookies, data):
    """Create create marshmallow book"""

    print(f"Cookies: {cookies}")
    print(f"Data: {data}")

    resp.text = "Stacked Marshmallow"


# Let's make an HTTP request to the server, to test it out.'}

# Media requests
r = api.requests.post("http://;/pybook", json={"price": 9.99, "title": "Pydantic book"})
print(r.text)

r = api.requests.post(
    "http://;/marshbook", json={"price": 10.99, "title": "Marshmallow book"}
)
print(r.text)


# Query(params) requests
r = api.requests.get("http://;/py_book_query?price=9.99&title=Pydantic book")
print(r.text)

r = api.requests.get("http://;/marsh_book_query?price=10.99&title=Marshmallow book")
print(r.text)


# Headers requests
r = api.requests.post("http://;/py_book_headers", headers={"x_version": "2.4.5"})
print(r.text)

r = api.requests.post("http://;/marsh_book_headers", headers={"X-Version": "2.5.6"})
print(r.text)


# Cookies requests
r = api.requests.post(
    "http://;/py_book_cookies", cookies={"max_age": "123", "is_cheap": "False"}
)
print(r.text)

r = api.requests.post(
    "http://;/marsh_book_cookies", cookies={"max_age": "123", "is_cheap": "True"}
)
print(r.text)


# Query and media requests demonstrating the use multiple decorators.
r = api.requests.post(
    "http://;/pystack?price=6.99&title=Query pydantic book",
    json={"price": 9.99, "title": "Pydantic book"},
)
print(r.text)

r = api.requests.post(
    "http://;/mashstack",
    json={"price": 10.99, "title": "Marshmallow book"},
    cookies={"max_age": "123", "is_cheap": "True"},
)
print(r.text)

# Route specs when using @expect
print(f"Spec is: {marshmallow_stacks._spec}")
