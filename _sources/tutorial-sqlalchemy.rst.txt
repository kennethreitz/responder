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

    api = responder.API(lifespan=lifespan)

This is the proper way to manage database connections in an async
application. The lifespan context manager ensures that:

1. Tables are created before the first request
2. The connection pool is properly closed when the server shuts down
3. If table creation fails, the server won't start


CRUD Endpoints
--------------

Now let's build the API endpoints. Each one opens a database session,
does its work, and commits or rolls back::

    from pydantic import BaseModel
    from sqlalchemy import select
    from database import async_session
    from models import Book

    # Pydantic models for request/response validation
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

        class Config:
            from_attributes = True

The ``from_attributes = True`` config tells Pydantic to read data from
SQLAlchemy model attributes (not just dicts). This lets you pass a
SQLAlchemy ``Book`` object directly to ``BookOut``.

**List all books**::

    @api.route("/books", methods=["GET"])
    async def list_books(req, resp):
        async with async_session() as session:
            result = await session.execute(select(Book))
            books = result.scalars().all()
            resp.media = [BookOut.model_validate(b).model_dump() for b in books]

**Create a book**::

    @api.route("/books", methods=["POST"], check_existing=False,
               request_model=BookIn, response_model=BookOut)
    async def create_book(req, resp):
        data = await req.media()

        async with async_session() as session:
            book = Book(**data)
            session.add(book)
            await session.commit()
            await session.refresh(book)
            resp.media = BookOut.model_validate(book).model_dump()
            resp.status_code = 201

**Get a single book**::

    @api.route("/books/{book_id:int}", methods=["GET"])
    async def get_book(req, resp, *, book_id):
        async with async_session() as session:
            book = await session.get(Book, book_id)
            if book is None:
                resp.status_code = 404
                resp.media = {"error": "Book not found"}
                return
            resp.media = BookOut.model_validate(book).model_dump()

**Update a book**::

    @api.route("/books/{book_id:int}", methods=["PUT"], check_existing=False,
               request_model=BookIn)
    async def update_book(req, resp, *, book_id):
        data = await req.media()

        async with async_session() as session:
            book = await session.get(Book, book_id)
            if book is None:
                resp.status_code = 404
                resp.media = {"error": "Book not found"}
                return

            for key, value in data.items():
                setattr(book, key, value)

            await session.commit()
            await session.refresh(book)
            resp.media = BookOut.model_validate(book).model_dump()

**Delete a book**::

    @api.route("/books/{book_id:int}", methods=["DELETE"], check_existing=False)
    async def delete_book(req, resp, *, book_id):
        async with async_session() as session:
            book = await session.get(Book, book_id)
            if book is None:
                resp.status_code = 404
                resp.media = {"error": "Book not found"}
                return

            await session.delete(book)
            await session.commit()
            resp.status_code = 204


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

- Use ``async with async_session() as session`` for every request.
  Don't share sessions across requests — each request should get its
  own session and transaction.

- For complex queries, use SQLAlchemy's ``select()`` with ``.where()``,
  ``.order_by()``, ``.limit()``, and ``.offset()`` — it composes
  naturally.

- In production, use connection pooling (SQLAlchemy does this by
  default) and set pool size limits appropriate for your database.

- Consider `Alembic <https://alembic.sqlalchemy.org/>`_ for database
  migrations — it tracks schema changes over time so you can evolve
  your database without losing data.
