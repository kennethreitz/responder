from typing import Optional

from marshmallow import Schema, fields
from pydantic import BaseModel
from sqlalchemy import Column, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

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


class Base(DeclarativeBase):
    pass


# Define an example SQLAlchemy model
class Book(Base):
    __tablename__ = "books"
    id = Column(Integer, primary_key=True)
    price = Column(Float)
    title = Column(String)


# Create tables in the database
engine = create_engine("sqlite:///:memory:", echo=True)
Base.metadata.create_all(engine)


# Create a session
Session = sessionmaker(bind=engine)
session = Session()

book1 = Book(price=9.99, title="Harry Potter")
book2 = Book(price=10.99, title="Pirates of the sea")
session.add(book1)
session.add(book2)
session.commit()


@api.schema("PydanticBookCreate")
class PydanticBookSchema(BaseModel):
    id: Optional[int]
    price: float
    title: str

    class Config:
        from_attributes = True


@api.schema("MarshmallowBookCreate")
class MarshmallowBookSchema(Schema):
    id = fields.Integer(dump_only=True)
    price = fields.Float()
    title = fields.Str()

    class Meta:
        model = Book


@api.route("/create")
@api.trust(MarshmallowBookSchema)
@api.ensure(MarshmallowBookSchema)
async def marshmallow_create(req, resp, *, data):
    "Create book"

    book = Book(**data)
    session.add(book)
    session.commit()

    return book


@api.route("/all")
@api.ensure(PydanticBookSchema)
async def all_books(req, resp):
    "Get all books"

    return session.query(Book)


r = api.requests.post(
    "http://;/create", json={"price": 11.99, "title": "Marshmallows book"}
)
print(r.text)

r = api.requests.post("http://;/all")
print(r.text)
