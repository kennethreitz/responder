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
    )

We're enabling OpenAPI documentation from the start. Visit ``/docs`` at
any point to see interactive Swagger UI for your API.


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

The first endpoint — list all books. This is a ``GET`` request to
``/books``::

    @api.route("/books", methods=["GET"], response_model=list)
    def list_books(req, resp):
        resp.media = list(books_db.values())

In REST API design, ``GET`` requests should never modify data. They're
*safe* and *idempotent* — calling them multiple times has the same effect
as calling them once.


Create a Book
-------------

To create a book, the client sends a ``POST`` request with a JSON body.
We use ``request_model=BookIn`` to validate the input automatically — if
the client sends bad data, they get a ``422`` response with error details::

    @api.route("/books", methods=["POST"], check_existing=False,
               request_model=BookIn, response_model=Book)
    async def create_book(req, resp):
        global next_id
        data = await req.media()

        book = {"id": next_id, **data}
        books_db[next_id] = book
        next_id += 1

        resp.media = book
        resp.status_code = 201

Note ``resp.status_code = 201`` — the HTTP ``201 Created`` status code
tells the client that a new resource was successfully created. This is
more informative than a generic ``200 OK``.


Get a Single Book
-----------------

Retrieve a specific book by its ID. The ``{book_id:int}`` route parameter
ensures only integer IDs match — requests like ``/books/abc`` will 404::

    @api.route("/books/{book_id:int}", methods=["GET"], response_model=Book)
    def get_book(req, resp, *, book_id):
        if book_id not in books_db:
            resp.status_code = 404
            resp.media = {"error": f"Book {book_id} not found"}
            return

        resp.media = books_db[book_id]


Update a Book
-------------

``PUT`` replaces a resource entirely. The client must send all fields::

    @api.route("/books/{book_id:int}", methods=["PUT"], check_existing=False,
               request_model=BookIn, response_model=Book)
    async def update_book(req, resp, *, book_id):
        if book_id not in books_db:
            resp.status_code = 404
            resp.media = {"error": f"Book {book_id} not found"}
            return

        data = await req.media()
        book = {"id": book_id, **data}
        books_db[book_id] = book
        resp.media = book


Delete a Book
-------------

``DELETE`` removes a resource. The convention is to return ``204 No Content``
with an empty body on success::

    @api.route("/books/{book_id:int}", methods=["DELETE"], check_existing=False)
    def delete_book(req, resp, *, book_id):
        if book_id not in books_db:
            resp.status_code = 404
            resp.media = {"error": f"Book {book_id} not found"}
            return

        del books_db[book_id]
        resp.status_code = 204


Error Handling
--------------

Let's add a custom error handler so any ``ValueError`` in our code returns
a clean JSON response instead of a 500 error::

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

This tutorial used in-memory storage. For a real application, you'll want
a database. See :doc:`tutorial-sqlalchemy` for how to integrate SQLAlchemy
with Responder using the lifespan pattern.
