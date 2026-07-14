"""Typed streaming values exposed by Responder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SSE(Generic[T]):
    """One typed Server-Sent Event with optional protocol metadata.

    Yield the data model directly when no metadata is needed. Use ``SSE`` for
    named events, resumable ids, retry hints, or comment-only keepalives::

        yield SSE(BuildEvent(...), event="progress", id=build.id)
        yield SSE(comment="still working")
    """

    data: T | None = None
    event: str | None = None
    id: str | int | None = None  # noqa: A003 - SSE protocol field name
    retry: int | None = None
    comment: str | None = None
