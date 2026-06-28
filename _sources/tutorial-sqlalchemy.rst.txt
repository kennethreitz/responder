Using SQLAlchemy
================

Most real web applications need a database. This guide shows how to
integrate `SQLAlchemy <https://www.sqlalchemy.org/>`_ with Responder,
using async support and the lifespan pattern for connection management.

SQLAlchemy is the most popular Python database toolkit. It gives you an
ORM (Object-Relational Mapper) for working with databases using Python
classes instead of raw SQL, plus a powerful query builder for when you
need fine-grained control.


Installation
------------

Install SQLAlchemy with async support and an async database driver.
We'll use SQLite for simplicity, but the pattern works with PostgreSQL,
MySQL, and any other database SQLAlchemy supports::

    $ uv pip install 'sqlalchemy[asyncio]' aiosqlite


Define Your Models
------------------

SQLAlchemy models map Python classes to database tables. Each attribute
becomes a column::

    # models.py
    from sqlalchemy import String
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

    class Base(DeclarativeBase):
        pass

    class Book(Base):
        __tablename__ = "books"

        id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
        title: Mapped[str] = mapped_column(String, nullable=False)
        author: Mapped[str] = mapped_column(String, nullable=False)
        year: Mapped[int] = mapped_column(nullable=False)
        isbn: Mapped[str | None] = mapped_column(String, nullable=True)

This uses SQLAlchemy 2.0's ``Mapped`` type annotations and
``mapped_column()``, which give you type checker support and cleaner
syntax than the older ``Column()`` style. Each model class corresponds
to a table, and each ``mapped_column()`` corresponds to a column.


Database Setup
--------------

Create the async engine and session factory. The *engine* manages
the connection pool. The *session* is your unit of work — you use it to
query and modify data within a transaction::

    # database.py
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    DATABASE_URL = "sqlite+aiosqlite:///./books.db"

    engine = create_async_engine(DATABASE_URL, echo=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

The ``echo=True`` flag prints all SQL queries to the console — very
helpful during development, but you'll want to disable it in production.

The ``expire_on_commit=False`` flag keeps model attributes accessible
after a commit, which is convenient for returning created objects in
API responses.


Lifespan for Startup and Shutdown
----------------------------------

Use Responder's lifespan context manager to create the database tables
on startup and dispose of connections on shutdown::

    # app.py
    from contextlib import asynccontextmanager
    import responder
    from database import engine
    from models import Base

    @asynccontextmanager
    async def lifespan(app):
        # Startup: create tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
        # Shutdown: close all connections
        await engine.dispose()

    api = responder.API(lifespan=lifespan, sessions=False)

This is the proper way to manage database connections in an async
application. The lifespan context manager ensures that:

1. Tables are created before the first request
2. The connection pool is properly closed when the server shuts down
3. If table creation fails, the server won't start

.. note::

    We pass ``sessions=False`` because this is a stateless REST API with
    no cookie sessions. Without it, Responder mints a random per-process
    signing key and logs a startup warning. If your app *does* use
    ``req.session``, set a stable ``secret_key=`` (or the
    ``RESPONDER_SECRET_KEY`` environment variable) instead — see the
    :doc:`tour` for the full story.


CRUD Endpoints
--------------

First, define the Pydantic schemas Responder uses to validate the request
body and shape the response::

    from pydantic import BaseModel, ConfigDict
    from sqlalchemy import select
    from responder import abort
    from database import async_session
    from models import Book

    class BookIn(BaseModel):
        title: str
        author: str
        year: int
        isbn: str | None = None

    class BookOut(BaseModel):
        model_config = ConfigDict(from_attributes=True)

        id: int
        title: str
        author: str
        year: int
        isbn: str | None = None

``from_attributes=True`` tells Pydantic to read values off SQLAlchemy
model attributes (not just dicts), so ``BookOut.model_validate(book)``
turns a ``Book`` ORM object straight into a response model.


A Session Per Request
~~~~~~~~~~~~~~~~~~~~~

Rather than open ``async with async_session()`` by hand in every handler,
register the session as a **dependency**. Each request gets its own,
cleaned up automatically::

    @api.dependency()
    async def session():
        async with async_session() as session:
            yield session

Responder injects this into any handler that declares a ``session``
parameter. The code after ``yield`` — here, the ``async with`` block
closing the session — runs as teardown once the response has been sent,
even if the handler raised. That's one session per request, with
guaranteed cleanup and no boilerplate in the views. A request-scoped
session layered on the module-level engine is the canonical dependency;
see the :doc:`tour` for the full dependency-injection guide.


The Handlers
~~~~~~~~~~~~

Each handler declares ``session`` to receive the injected session. On
write methods, a Pydantic-typed parameter (``book: BookIn``) is
auto-filled with the validated request body — an invalid body returns
``422`` before the handler runs, so there's no manual ``await req.media()``
or ``request_model=`` to wire up. A ``-> BookOut`` return annotation makes
Responder validate and serialize ``resp.media`` against that model, and
both models flow into the generated :doc:`OpenAPI schema <tour>`
automatically.

**List all books**::

    @api.route("/books", methods=["GET"])
    async def list_books(req, resp, *, session) -> list[BookOut]:
        result = await session.execute(select(Book))
        books = result.scalars().all()
        resp.media = [BookOut.model_validate(b) for b in books]

**Create a book**::

    @api.route("/books", methods=["POST"], check_existing=False)
    async def create_book(req, resp, *, book: BookIn, session) -> BookOut:
        new = Book(**book.model_dump())
        session.add(new)
        await session.commit()
        await session.refresh(new)
        resp.media = BookOut.model_validate(new)
        resp.status_code = 201

**Get a single book**::

    @api.route("/books/{book_id:int}", methods=["GET"])
    async def get_book(req, resp, *, book_id, session) -> BookOut:
        book = await session.get(Book, book_id)
        if book is None:
            abort(404, detail="Book not found")
        resp.media = BookOut.model_validate(book)

**Update a book**::

    @api.route("/books/{book_id:int}", methods=["PUT"], check_existing=False)
    async def update_book(req, resp, *, book_id, book: BookIn, session) -> BookOut:
        existing = await session.get(Book, book_id)
        if existing is None:
            abort(404, detail="Book not found")

        for key, value in book.model_dump().items():
            setattr(existing, key, value)

        await session.commit()
        await session.refresh(existing)
        resp.media = BookOut.model_validate(existing)

**Delete a book**::

    @api.route("/books/{book_id:int}", methods=["DELETE"], check_existing=False)
    async def delete_book(req, resp, *, book_id, session):
        book = await session.get(Book, book_id)
        if book is None:
            abort(404, detail="Book not found")

        await session.delete(book)
        await session.commit()
        resp.status_code = 204

A couple of things worth noting. ``abort(404, detail="Book not found")``
raises a rendered, content-negotiated HTTP error and halts the handler
immediately — no need to set ``resp.status_code`` and ``return`` by hand.
And assigning a model (or a list of them) to ``resp.media`` just works:
Responder serializes Pydantic models natively, so the trailing
``.model_dump()`` is no longer needed.

.. note::

    The ``-> BookOut`` return validation only fires when ``resp.media`` is
    a dict or a Pydantic model. A raw SQLAlchemy ORM object assigned to
    ``resp.media`` is **not** auto-validated — that's why every handler
    wraps the result with ``BookOut.model_validate(book)`` before
    returning it.


Run It
------

::

    if __name__ == "__main__":
        api.run()

Start the server and you'll see SQLAlchemy's SQL echo in the console.
The SQLite database file ``books.db`` is created automatically on first
startup.


Using PostgreSQL
----------------

To switch to PostgreSQL, just change the connection URL and driver::

    $ uv pip install asyncpg

::

    DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/mydb"

Everything else stays the same. SQLAlchemy abstracts the database
differences so your application code doesn't need to change.


Tips
----

- Give each request its own session and transaction — never share one
  across requests. The ``session`` dependency above does this for you;
  declaring ``session`` in a handler is all it takes.

- For complex queries, use SQLAlchemy's ``select()`` with ``.where()``,
  ``.order_by()``, ``.limit()``, and ``.offset()`` — it composes
  naturally.

- In production, use connection pooling (SQLAlchemy does this by
  default) and set pool size limits appropriate for your database.

- Consider `Alembic <https://alembic.sqlalchemy.org/>`_ for database
  migrations — it tracks schema changes over time so you can evolve
  your database without losing data.
