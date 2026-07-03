"""Pagination helpers: a ``Page`` envelope model and a ``paginate()`` helper.

Pair these with the typed ``Query`` markers for page-number pagination::

    from responder import Query
    from responder.ext.pagination import Page, paginate, set_pagination_headers

    @api.get("/items", response_model=Page[Item])
    def list_items(req, resp, *,
                   page: int = Query(1, ge=1),
                   size: int = Query(20, ge=1, le=100)):
        result = paginate(db.all(), page=page, size=size)
        set_pagination_headers(req, resp, result)
        resp.media = result

``paginate`` slices an in-memory collection by default; pass ``total=`` when you
have already sliced the page yourself (e.g. with a ``LIMIT/OFFSET`` query).
``set_pagination_headers`` additionally emits the GitHub-style ``Link``
(:rfc:`8288`) and ``X-Total-Count`` response headers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Generic, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

T = TypeVar("T")

__all__ = ["Page", "paginate", "set_pagination_headers"]


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


def paginate(
    items: Iterable[Any], *, page: int = 1, size: int = 20, total: int | None = None
) -> Page:
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


def _page_url(url: str, page: int, size: int) -> str:
    """``url`` with its ``page``/``size`` query parameters replaced.

    All other query parameters (filters, sort specs, …) are preserved.
    """
    scheme, netloc, path, query, fragment = urlsplit(url)
    # keep_blank_values so '?q=&page=2' preserves the blank-valued 'q='.
    params = [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if k not in ("page", "size")
    ]
    params += [("page", str(page)), ("size", str(size))]
    return urlunsplit((scheme, netloc, path, urlencode(params), fragment))


def set_pagination_headers(req: Any, resp: Any, page: Page) -> None:
    """Emit :rfc:`8288` ``Link`` and ``X-Total-Count`` headers for ``page``.

    Sets the GitHub-style navigation headers most REST consumers expect::

        X-Total-Count: 105
        Link: <https://…/items?page=1&size=20>; rel="first",
              <https://…/items?page=2&size=20>; rel="prev",
              <https://…/items?page=4&size=20>; rel="next",
              <https://…/items?page=6&size=20>; rel="last"

    Links are built from the request's own URL, so every other query
    parameter (filters, sort specs, …) is preserved; only ``page`` and
    ``size`` are rewritten. ``rel="prev"`` is omitted on the first page and
    ``rel="next"`` on (or beyond) the last.

    Usage::

        result = paginate(db.all(), page=page, size=size)
        set_pagination_headers(req, resp, result)
        resp.media = result

    :param req: The current :class:`~responder.models.Request`.
    :param resp: The :class:`~responder.models.Response` to set headers on.
    :param page: The :class:`Page` envelope returned by :func:`paginate`.
    """
    url = req.full_url
    last = max(page.pages, 1)
    links = [(1, "first")]
    if page.page > 1:
        links.append((min(page.page - 1, last), "prev"))
    if page.page < page.pages:
        links.append((page.page + 1, "next"))
    links.append((last, "last"))
    resp.headers["Link"] = ", ".join(
        f'<{_page_url(url, number, page.size)}>; rel="{rel}"'
        for number, rel in links
    )
    resp.headers["X-Total-Count"] = str(page.total)
