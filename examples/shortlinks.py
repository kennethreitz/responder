"""A short link API with redirects and click tracking.

Run it:

    responder run examples/shortlinks.py

Try it with:

    curl http://127.0.0.1:5042/links
    curl -H "Content-Type: application/json" \
         -d '{"code": "docs", "destination": "https://responder.kennethreitz.org"}' \
         http://127.0.0.1:5042/links
    curl -i http://127.0.0.1:5042/r/docs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

import responder


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _normalize_code(code: str) -> str:
    return code.strip().lower()


def _base36(number: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = ""
    while number:
        number, remainder = divmod(number, 36)
        value = alphabet[remainder] + value
    return value or "0"


class LinkCreate(BaseModel):
    destination: str = Field(
        pattern=r"^https?://",
        max_length=2048,
        examples=["https://responder.kennethreitz.org"],
    )
    code: str | None = Field(
        default=None,
        pattern=r"^[a-zA-Z0-9_-]{3,32}$",
        examples=["docs"],
    )
    title: str = Field(default="", max_length=120, examples=["Responder docs"])

    @field_validator("code")
    @classmethod
    def normalize_code(cls, code: str | None) -> str | None:
        return None if code is None else _normalize_code(code)


class LinkOut(BaseModel):
    code: str
    destination: str
    title: str
    short_url: str
    clicks: int
    created_at: datetime
    last_clicked_at: datetime | None


LINK_EXAMPLE = {
    "code": "docs",
    "destination": "https://responder.kennethreitz.org",
    "title": "Responder docs",
    "short_url": "/r/docs",
    "clicks": 0,
    "created_at": "2026-06-30T12:00:00Z",
    "last_clicked_at": None,
}


@dataclass
class LinkStore:
    links: dict[str, LinkOut] = field(default_factory=dict)
    next_id: int = 1

    @classmethod
    def seeded(cls) -> LinkStore:
        store = cls()
        store.create(
            LinkCreate(
                code="docs",
                title="Responder docs",
                destination="https://responder.kennethreitz.org",
            )
        )
        store.create(
            LinkCreate(
                code="repo",
                title="Responder on GitHub",
                destination="https://github.com/kennethreitz/responder",
            )
        )
        return store

    def list(self) -> list[LinkOut]:
        return [
            link.model_copy(deep=True)
            for link in sorted(self.links.values(), key=lambda item: item.code)
        ]

    def get(self, code: str) -> LinkOut | None:
        link = self.links.get(_normalize_code(code))
        return None if link is None else link.model_copy(deep=True)

    def create(self, link: LinkCreate) -> LinkOut:
        code = link.code or self._next_code()
        if code in self.links:
            raise ValueError(f"Short code {code!r} already exists.")

        record = LinkOut(
            code=code,
            destination=link.destination,
            title=link.title,
            short_url=f"/r/{code}",
            clicks=0,
            created_at=_now(),
            last_clicked_at=None,
        )
        self.links[code] = record
        return record.model_copy(deep=True)

    def record_click(self, code: str) -> LinkOut | None:
        normalized = _normalize_code(code)
        link = self.links.get(normalized)
        if link is None:
            return None

        clicked = link.model_copy(
            update={"clicks": link.clicks + 1, "last_clicked_at": _now()}
        )
        self.links[normalized] = clicked
        return clicked.model_copy(deep=True)

    def _next_code(self) -> str:
        while True:
            code = _base36(self.next_id).rjust(3, "0")
            self.next_id += 1
            if code not in self.links:
                return code


def create_api(*, store: LinkStore | None = None) -> responder.API:
    store = store or LinkStore.seeded()
    api = responder.API(
        title="Short Links API",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
    )

    @api.get("/", include_in_schema=False)
    def index(req, resp):
        resp.media = {
            "name": "Short Links API",
            "links": "/links",
            "redirect": "/r/{code}",
            "docs": "/docs",
        }

    @api.get(
        "/links",
        operation_id="list_short_links",
        tags=["short-links"],
        summary="List short links",
        response_model=list[LinkOut],
        examples={"seed": {"value": [LINK_EXAMPLE]}},
    )
    def list_links(req, resp):
        resp.media = store.list()

    @api.post(
        "/links",
        operation_id="create_short_link",
        tags=["short-links"],
        summary="Create a short link",
        response_model=LinkOut,
        responses={201: "Short link created", 409: "Code already exists"},
        response_examples={201: {"created": {"value": LINK_EXAMPLE}}},
    )
    def create_link(req, resp, *, link: LinkCreate):
        try:
            created = store.create(link)
        except ValueError as exc:
            resp.problem(
                409,
                str(exc),
                type="https://responder.example/problems/short-code-conflict",
            )
            return
        resp.created(created, location=f"/links/{created.code}")

    @api.get(
        "/links/{code}",
        operation_id="get_short_link",
        tags=["short-links"],
        summary="Fetch short link details",
        response_model=LinkOut,
        responses={404: "Short link not found"},
    )
    def get_link(req, resp, *, code: str):
        link = store.get(code)
        if link is None:
            resp.problem(404, f"Short link {code!r} does not exist.", code=code)
            return
        resp.media = link

    @api.get(
        "/r/{code}",
        operation_id="follow_short_link",
        tags=["short-links"],
        summary="Redirect through a short link",
        responses={302: "Redirect", 404: "Short link not found"},
    )
    def follow_link(req, resp, *, code: str):
        link = store.record_click(code)
        if link is None:
            resp.problem(404, f"Short link {code!r} does not exist.", code=code)
            return
        resp.redirect(link.destination, status_code=302)

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
