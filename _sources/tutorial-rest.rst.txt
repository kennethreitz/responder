Building a REST API
===================

This tutorial walks you through building a complete REST API from scratch.
By the end, you'll have a working API with CRUD operations, request
validation, error handling, and interactive documentation.

We'll build a simple book catalog — a service that lets you create, read,
update, and delete books.


Project Setup
-------------

Create a new file called ``app.py``::

    import responder

    api = responder.API(
        title="Book Catalog",
        version="1.0",
        openapi="3.0.2",
        docs_route="/docs",
        sessions=False,
    )

We enable OpenAPI documentation from the start. ``openapi="3.0.2"`` serves the
machine-readable schema at ``/schema.yml``, and ``docs_route="/docs"`` mounts
interactive Swagger UI on top of it — visit ``/docs`` at any point to explore
and exercise your API from the browser.

``sessions=False`` tells Responder this service is stateless. Without it,
Responder's secure-by-default sessions would mint a throwaway signing key on
startup and warn you about it; since we never touch ``req.session``, we simply
switch them off. See :doc:`guide-config` for sessions and secret keys.


Define Your Models
------------------

We'll use `Pydantic <https://docs.pydantic.dev/>`_ to define our data
models. Pydantic models serve double duty — they validate incoming data
*and* generate OpenAPI schemas automatically::

    from pydantic import BaseModel

    class BookIn(BaseModel):
        """What the client sends when creating a book."""
        title: str
        author: str
        year: int
        isbn: str | None = None

    class Book(BaseModel):
        """What the API returns."""
        id: int
        title: str
        author: str
        year: int
        isbn: str | None = None

``BookIn`` is the *input* model — it doesn't have an ``id`` because the
server assigns that. ``Book`` is the *output* model — it includes
everything. This input/output separation is a common REST API pattern.


In-Memory Storage
-----------------

For this tutorial, we'll store books in a simple dict. In a real
application, you'd use a database (see :doc:`tutorial-sqlalchemy`)::

    books_db: dict[int, dict] = {}
    next_id = 1


List All Books
--------------

The first endpoint lists every book. It's a ``GET`` request to ``/books``,
with an optional ``?author=`` filter wired up as a typed query parameter::

    from responder import Query

    @api.route("/books", methods=["GET"])
    def list_books(req, resp, *, author: str | None = Query(None)) -> list[Book]:
        books = list(books_db.values())
        if author:
            books = [b for b in books if b["author"] == author]
        resp.media = books

``Query(None)`` declares an *optional* query parameter — use ``Query(...)`` to
make one required, in which case a missing or mis-typed value returns a ``422``
automatically. Responder reads ``?author=...`` from the query string, coerces
it to the annotated type, and passes it to your handler as a keyword argument.
The ``-> list[Book]`` return annotation documents the response shape.

In REST API design, ``GET`` requests should never modify data. They're *safe*
and *idempotent* — calling them many times has the same effect as calling them
once.


Create a Book
-------------

To create a book, the client sends a ``POST`` request with a JSON body.
Annotate a parameter with your input model and Responder validates the body
for you, handing the handler a parsed ``BookIn`` — no manual ``req.media()``,
and an automatic ``422`` with error details when the data is bad::

    @api.route("/books", methods=["POST"], check_existing=False)
    def create_book(req, resp, *, data: BookIn) -> Book:
        global next_id

        book = {"id": next_id, **data.model_dump()}
        books_db[next_id] = book
        next_id += 1

        return book, 201

``data`` arrives as a validated ``BookIn`` instance, so we call
``data.model_dump()`` to turn it back into a plain dict. Returning
``book, 201`` is Flask-style shorthand: the first item becomes the response
body and the second the status code — here ``201 Created``, which tells the
client a new resource was created (more informative than a generic ``200 OK``).
The ``-> Book`` return annotation runs the outgoing payload through the ``Book``
model, coercing types and stripping any field the model doesn't declare.

.. note::

   Prefer to keep the body off the signature? The explicit
   ``request_model=BookIn`` / ``response_model=Book`` route kwargs still work
   and feed the same validation and OpenAPI schema. See the :doc:`tour` for
   both styles side by side.


Get a Single Book
-----------------

Retrieve a book by its ID. The ``{book_id:int}`` converter ensures only
integer IDs match, so ``/books/abc`` 404s before your handler even runs::

    @api.route("/books/{book_id:int}", methods=["GET"])
    def get_book(req, resp, *, book_id) -> Book:
        if book_id not in books_db:
            responder.abort(404, detail=f"Book {book_id} not found")

        resp.media = books_db[book_id]

``responder.abort()`` raises a proper HTTP error from anywhere in your code —
no Starlette imports, no juggling ``resp.status_code`` by hand. Responder
renders it as JSON (``{"error": "..."}``) for clients that ask for JSON and as
plain text otherwise.


Update a Book
-------------

``PUT`` replaces a resource entirely, so the client sends every field. The same
body injection and ``abort`` apply here::

    @api.route("/books/{book_id:int}", methods=["PUT"], check_existing=False)
    def update_book(req, resp, *, book_id, data: BookIn) -> Book:
        if book_id not in books_db:
            responder.abort(404, detail=f"Book {book_id} not found")

        book = {"id": book_id, **data.model_dump()}
        books_db[book_id] = book
        return book

``book_id`` comes from the URL and ``data`` from the request body — Responder
injects both as keyword arguments.


Delete a Book
-------------

``DELETE`` removes a resource. The convention is to return ``204 No Content``
with an empty body on success::

    @api.route("/books/{book_id:int}", methods=["DELETE"], check_existing=False)
    def delete_book(req, resp, *, book_id):
        if book_id not in books_db:
            responder.abort(404, detail=f"Book {book_id} not found")

        del books_db[book_id]
        resp.status_code = 204


Error Handling
--------------

``responder.abort()`` handles HTTP errors, but you can also map your own
exception types to clean responses. Here, any ``ValueError`` that escapes a
handler becomes a tidy ``400`` instead of a generic ``500``::

    @api.exception_handler(ValueError)
    async def handle_value_error(req, resp, exc):
        resp.status_code = 400
        resp.media = {"error": str(exc)}


Run It
------

Add the standard entry point at the bottom of your file::

    if __name__ == "__main__":
        api.run()

Start the server::

    $ python app.py

Visit ``http://localhost:5042/docs`` to see your interactive API
documentation. You can test every endpoint directly from the browser.


Try It Out
----------

Using ``curl``::

    # Create a book
    $ curl -X POST http://localhost:5042/books \
        -H "Content-Type: application/json" \
        -d '{"title": "Dune", "author": "Frank Herbert", "year": 1965}'

    # List all books
    $ curl http://localhost:5042/books

    # Filter by author
    $ curl "http://localhost:5042/books?author=Frank+Herbert"

    # Get a specific book
    $ curl http://localhost:5042/books/1

    # Update a book
    $ curl -X PUT http://localhost:5042/books/1 \
        -H "Content-Type: application/json" \
        -d '{"title": "Dune", "author": "Frank Herbert", "year": 1965, "isbn": "978-0441172719"}'

    # Delete a book
    $ curl -X DELETE http://localhost:5042/books/1


What's Next
-----------

This tutorial used in-memory storage. For a real application, you'll want a
database. See :doc:`tutorial-sqlalchemy` for integrating SQLAlchemy with
Responder using the lifespan pattern and per-request dependency injection.

For the full picture on typed parameters, Pydantic validation, dependency
injection, and OpenAPI generation, see the :doc:`tour`.
