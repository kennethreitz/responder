# Complete REST API example with Pydantic validation.
# https://responder.kennethreitz.org/tutorial-rest.html
import responder
from pydantic import BaseModel


class BookIn(BaseModel):
    title: str
    author: str
    year: int
    isbn: str | None = None


class BookOut(BaseModel):
    id: int
    title: str
    author: str
    year: int
    isbn: str | None = None


api = responder.API(
    title="Book Catalog",
    version="1.0",
    openapi="3.0.2",
    docs_route="/docs",
)

books_db: dict[int, dict] = {}
next_id = 1


@api.route("/books", methods=["GET"])
def list_books(req, resp):
    resp.media = list(books_db.values())


@api.route("/books", methods=["POST"], check_existing=False,
           request_model=BookIn, response_model=BookOut)
async def create_book(req, resp):
    global next_id
    data = await req.media()
    book = {"id": next_id, **data}
    books_db[next_id] = book
    next_id += 1
    resp.media = book
    resp.status_code = 201


@api.route("/books/{book_id:int}", methods=["GET"])
def get_book(req, resp, *, book_id):
    if book_id not in books_db:
        resp.status_code = 404
        resp.media = {"error": f"Book {book_id} not found"}
        return
    resp.media = books_db[book_id]


@api.route("/books/{book_id:int}", methods=["DELETE"], check_existing=False)
def delete_book(req, resp, *, book_id):
    if book_id not in books_db:
        resp.status_code = 404
        resp.media = {"error": f"Book {book_id} not found"}
        return
    del books_db[book_id]
    resp.status_code = 204


if __name__ == "__main__":
    api.run()
