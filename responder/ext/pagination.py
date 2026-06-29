"""Pagination helpers: a ``Page`` envelope model and a ``paginate()`` helper.

Pair these with the typed ``Query`` markers for page-number pagination::

    from responder import Query
    from responder.ext.pagination import Page, paginate

    @api.get("/items", response_model=Page[Item])
    def list_items(req, resp, *,
                   page: int = Query(1, ge=1),
                   size: int = Query(20, ge=1, le=100)):
        resp.media = paginate(db.all(), page=page, size=size)

``paginate`` slices an in-memory collection by default; pass ``total=`` when you
have already sliced the page yourself (e.g. with a ``LIMIT/OFFSET`` query).
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

__all__ = ["Page", "paginate"]


class Page(BaseModel, Generic[T]):
    """A page of results plus pagination metadata.

    Use as a response model — ``response_model=Page[Item]`` — to document and
    validate the envelope.
    """

    items: list[T]
    total: int
    page: int
    size: int
    pages: int


def paginate(items, *, page: int = 1, size: int = 20, total: int | None = None) -> Page:
    """Wrap ``items`` in a :class:`Page`.

    :param items: The results. If ``total`` is ``None`` this is treated as the
        full collection and sliced for ``page``; otherwise it is assumed to
        already be the page's slice.
    :param page: 1-based page number.
    :param size: Page size.
    :param total: Overall item count (defaults to ``len(items)`` when slicing).
    """
    page = max(page, 1)
    size = max(size, 1)
    if total is None:
        seq = list(items)
        total = len(seq)
        start = (page - 1) * size
        page_items = seq[start : start + size]
    else:
        page_items = list(items)
    pages = (total + size - 1) // size
    return Page(items=page_items, total=total, page=page, size=size, pages=pages)
