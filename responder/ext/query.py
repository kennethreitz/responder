"""Sorting and filtering helpers for in-memory list endpoints.

These complement :mod:`responder.ext.pagination`. They operate on an in-memory
sequence of dicts or objects — no ORM/database coupling. Drive them from the
typed ``Query`` markers so values are validated before they reach here::

    from responder import Query
    from responder.ext.query import filter_items, sort_items
    from responder.ext.pagination import Page, paginate

    @api.get("/items", response_model=Page[Item])
    def list_items(req, resp, *,
                   status: str = Query(None),
                   sort: str = Query("name"),
                   page: int = Query(1, ge=1),
                   size: int = Query(20, ge=1, le=100)):
        rows = filter_items(db.all(), {"status": status})
        rows = sort_items(rows, sort, allowed={"name", "created_at"})
        resp.media = paginate(rows, page=page, size=size)
"""

from __future__ import annotations

from typing import Any, Callable

from starlette.exceptions import HTTPException

__all__ = ["parse_sort", "sort_items", "filter_items"]


def _value(item, field):
    """Read ``field`` from a dict or object item."""
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def _sort_key(field: str) -> Callable[[Any], tuple[bool, Any]]:
    """A sort key for ``field`` that puts ``None`` values last."""

    def key(item: Any) -> tuple[bool, Any]:
        value = _value(item, field)
        return (value is None, value)

    return key


def parse_sort(
    spec: str | None, *, allowed: Any = None
) -> list[tuple[str, bool]]:
    """Parse a sort spec into ``[(field, descending), ...]``.

    ``spec`` is a comma-separated list of fields; a leading ``-`` means
    descending (``"name,-created"`` → name asc, created desc). When ``allowed``
    is given, a field outside it raises ``HTTPException(400)`` — pass it for any
    client-supplied sort so users can't order by arbitrary attributes.
    """
    order: list[tuple[str, bool]] = []
    if not spec:
        return order
    allowed = set(allowed) if allowed is not None else None
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        descending = token[0] == "-"
        field = token[1:] if token[0] in "+-" else token
        if allowed is not None and field not in allowed:
            raise HTTPException(status_code=400, detail=f"Cannot sort by {field!r}")
        order.append((field, descending))
    return order


def sort_items(items: Any, spec: str | None, *, allowed: Any = None) -> list:
    """Return a new list sorted by a sort spec (see :func:`parse_sort`).

    ``None`` values sort last. A field whose values aren't mutually comparable
    raises ``HTTPException(400)`` rather than a server error.
    """
    order = parse_sort(spec, allowed=allowed)
    result = list(items)
    # Apply keys right-to-left; Python's stable sort yields correct precedence.
    for field, descending in reversed(order):
        try:
            result.sort(key=_sort_key(field), reverse=descending)
        except TypeError as exc:
            raise HTTPException(
                status_code=400, detail=f"Cannot sort by {field!r}: incomparable values"
            ) from exc
    return result


def filter_items(items: Any, filters: dict) -> list:
    """Filter ``items`` by equality against a ``{field: value}`` mapping.

    Entries whose value is ``None`` are skipped, so you can pass optional
    ``Query`` markers straight through::

        filter_items(rows, {"status": status, "category": category})

    Values are compared with ``==`` against each item's field, so drive it from
    typed markers (which coerce to the right type) rather than raw strings.
    """
    active = {k: v for k, v in filters.items() if v is not None}
    if not active:
        return list(items)
    return [
        item
        for item in items
        if all(_value(item, field) == value for field, value in active.items())
    ]
