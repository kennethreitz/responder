"""A complete REST API example with Pydantic validation.

Run it:

    responder run examples/rest_api.py

Try it with:

    curl http://127.0.0.1:5042/books
    curl -H "Content-Type: application/json" \
         -d '{"title": "Kindred", "author": "Octavia E. Butler", "year": 1979}' \
         http://127.0.0.1:5042/books
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

import responder


class BookIn(BaseModel):
    title: str = Field(min_length=1, examples=["The Left Hand of Darkness"])
    author: str = Field(min_length=1, examples=["Ursula K. Le Guin"])
    year: int = Field(ge=0, examples=[1969])
    isbn: str | None = Field(default=None, examples=["9780441478125"])


class BookOut(BookIn):
    id: int


BOOK_EXAMPLE = {
    "id": 1,
    "title": "The Left Hand of Darkness",
    "author": "Ursula K. Le Guin",
    "year": 1969,
    "isbn": "9780441478125",
}


@dataclass
class BookStore:
    books: dict[int, BookOut] = field(default_factory=dict)
    next_id: int = 1

    @classmethod
    def seeded(cls) -> BookStore:
        store = cls()
        store.create(
            BookIn(
                title="The Left Hand of Darkness",
                author="Ursula K. Le Guin",
                year=1969,
                isbn="9780441478125",
            )
        )
        store.create(
            BookIn(
                title="Kindred",
                author="Octavia E. Butler",
                year=1979,
                isbn="9780807083697",
            )
        )
        return store

    def list(self, *, author: str | None = None) -> list[BookOut]:
        rows = sorted(self.books.values(), key=lambda item: item.id)
        if author:
            wanted = author.casefold()
            rows = [book for book in rows if wanted in book.author.casefold()]
        return [book.model_copy(deep=True) for book in rows]

    def get(self, book_id: int) -> BookOut | None:
        book = self.books.get(book_id)
        return None if book is None else book.model_copy(deep=True)

    def create(self, book: BookIn) -> BookOut:
        book_id = self.next_id
        self.next_id += 1
        record = BookOut(id=book_id, **book.model_dump())
        self.books[book_id] = record
        return record.model_copy(deep=True)

    def delete(self, book_id: int) -> bool:
        return self.books.pop(book_id, None) is not None


def create_api(*, store: BookStore | None = None) -> responder.API:
    store = store or BookStore.seeded()
    api = responder.API(
        title="Book Catalog",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
    )

    @api.get("/", include_in_schema=False)
    def index(req, resp):
        resp.media = {"name": "Book Catalog", "books": "/books", "docs": "/docs"}

    @api.get(
        "/books",
        operation_id="list_books",
        tags=["books"],
        summary="List books",
        response_model=list[BookOut],
        examples={"seed": {"value": [BOOK_EXAMPLE]}},
    )
    def list_books(
        req,
        resp,
        *,
        author: str | None = responder.Query(
            None,
            description="Filter books by author name.",
        ),
    ):
        resp.media = store.list(author=author)

    @api.post(
        "/books",
        operation_id="create_book",
        tags=["books"],
        summary="Create a book",
        response_model=BookOut,
        responses={201: "Book created"},
        response_examples={201: {"created": {"value": BOOK_EXAMPLE}}},
    )
    def create_book(req, resp, *, book: BookIn):
        created = store.create(book)
        resp.created(created, location=f"/books/{created.id}")

    @api.get(
        "/books/{book_id:int}",
        operation_id="get_book",
        tags=["books"],
        summary="Fetch a book",
        response_model=BookOut,
        responses={404: "Book not found"},
        examples={"left_hand": {"value": BOOK_EXAMPLE}},
    )
    def get_book(req, resp, *, book_id: int):
        book = store.get(book_id)
        if book is None:
            resp.problem(404, f"Book {book_id} does not exist.", book_id=book_id)
            return
        resp.media = book

    @api.delete(
        "/books/{book_id:int}",
        operation_id="delete_book",
        tags=["books"],
        summary="Delete a book",
        responses={204: "Book deleted", 404: "Book not found"},
    )
    def delete_book(req, resp, *, book_id: int):
        if not store.delete(book_id):
            resp.problem(404, f"Book {book_id} does not exist.", book_id=book_id)
            return
        resp.no_content(headers={"X-Deleted-Book": str(book_id)})

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
