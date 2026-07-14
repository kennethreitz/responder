"""Responder 9.2 typed SSE and NDJSON stream contracts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
import yaml
from pydantic import BaseModel

import responder
from responder.ext.ratelimit import RateLimiter
from responder.testing import AsyncTestClient


class Event(BaseModel):
    id: int
    message: str


def make_api(*, openapi: str | None = "3.1.0", **kwargs) -> responder.API:
    return responder.API(
        title="Streams",
        version="1",
        openapi=openapi,
        allowed_hosts=[";"],
        sessions=False,
        **kwargs,
    )


def test_typed_sse_infers_validates_and_serializes_event_data():
    api = make_api()

    @api.sse("/events")
    async def events(req, resp) -> AsyncIterator[Event]:
        yield {"id": "1", "message": "ready", "extra": "drop"}

    response = api.requests.get("/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text == 'data: {"id": 1, "message": "ready"}\n\n'


def test_sse_envelope_preserves_protocol_metadata_and_comments():
    api = make_api()

    @api.sse("/events")
    async def events(req, resp) -> AsyncIterator[responder.SSE[Event]]:
        yield responder.SSE(
            Event(id=1, message="ready"), event="build", id="job-1", retry=1500
        )
        yield responder.SSE(comment="still working")

    response = api.requests.get("/events")

    assert response.text == (
        "event: build\n"
        "id: job-1\n"
        "retry: 1500\n"
        'data: {"id": 1, "message": "ready"}\n\n'
        ": still working\n\n"
    )


def test_typed_ndjson_supports_async_and_sync_iterators():
    api = make_api()

    @api.ndjson("/async")
    async def async_items(req, resp) -> AsyncIterator[Event]:
        yield {"id": "1", "message": "one"}
        yield Event(id=2, message="two")

    @api.ndjson("/sync")
    def sync_items(req, resp) -> Iterator[Event]:
        yield Event(id=3, message="three")

    response = api.requests.get("/async")
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.text.splitlines() == [
        '{"id":1,"message":"one"}',
        '{"id":2,"message":"two"}',
    ]
    assert api.requests.get("/sync").json() == {"id": 3, "message": "three"}


def test_explicit_item_model_accepts_a_returned_async_iterable():
    api = make_api()

    @api.ndjson("/events", item_model=Event)
    async def events(req, resp):
        async def generate():
            yield {"id": 1, "message": "ready"}

        return generate()

    assert api.requests.get("/events").text == '{"id":1,"message":"ready"}\n'


def test_first_invalid_item_fails_before_headers_are_sent():
    api = make_api()

    @api.ndjson("/events")
    async def events(req, resp) -> AsyncIterator[Event]:
        yield {"id": "invalid", "message": "broken"}

    response = api.requests.get("/events")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Internal Server Error"
    assert response.content != b'{"id":"invalid","message":"broken"}\n'


def test_later_invalid_item_terminates_and_logs_without_emitting_it(caplog):
    api = make_api()

    @api.ndjson("/events")
    async def events(req, resp) -> AsyncIterator[Event]:
        yield Event(id=1, message="good")
        yield {"id": "invalid", "message": "broken"}
        yield Event(id=3, message="never sent")

    with caplog.at_level("ERROR", logger="responder"):
        response = api.requests.get("/events")

    assert response.status_code == 200
    assert response.text == '{"id":1,"message":"good"}\n'
    assert "Stream contract failed" in caplog.text


def test_empty_stream_is_valid_and_head_does_not_start_the_producer():
    api = make_api()
    started = False

    @api.sse("/events")
    async def events(req, resp) -> AsyncIterator[Event]:
        nonlocal started
        started = True
        if False:
            yield Event(id=1, message="never")

    head = api.requests.head("/events")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"].startswith("text/event-stream")
    assert started is False

    get = api.requests.get("/events")
    assert get.status_code == 200
    assert get.content == b""
    assert started is True


@pytest.mark.parametrize("kind", ["sse", "ndjson"])
def test_typed_stream_requires_an_inferable_or_explicit_model(kind):
    api = make_api()
    decorator = getattr(api, kind)("/events")

    with pytest.raises(TypeError, match="iterator return annotation"):

        @decorator
        async def events(req, resp):
            return None


def test_typed_stream_openapi_exposes_wire_and_item_schemas():
    api = make_api()

    @api.sse("/events", operation_id="stream_events")
    async def events(req, resp) -> AsyncIterator[responder.SSE[Event]]:
        yield responder.SSE(Event(id=1, message="ready"))

    @api.ndjson("/rows", item_model=list[int])
    async def rows(req, resp):
        yield [1, 2]

    spec = yaml.safe_load(api.requests.get("/schema.yml").content)
    event_response = spec["paths"]["/events"]["get"]["responses"]["200"]
    event_media = event_response["content"]["text/event-stream"]
    assert event_media["schema"] == {"type": "string"}
    assert event_media["x-responder-item-schema"] == {
        "$ref": "#/components/schemas/Event"
    }
    assert event_response["x-responder-stream"]["mode"] == "sse"
    assert spec["components"]["schemas"]["Event"]["properties"]["id"][
        "type"
    ] == "integer"

    row_response = spec["paths"]["/rows"]["get"]["responses"]["200"]
    row_item = row_response["content"]["application/x-ndjson"][
        "x-responder-item-schema"
    ]
    assert row_item == {"type": "array", "items": {"type": "integer"}}


def test_invalid_sse_metadata_fails_the_contract():
    api = make_api()

    @api.sse("/events")
    async def events(req, resp) -> AsyncIterator[responder.SSE[Event]]:
        yield responder.SSE(Event(id=1, message="ready"), retry=-1)

    assert api.requests.get("/events").status_code == 500


def test_typed_sse_heartbeat_starts_before_a_slow_first_event_and_cancels():
    api = make_api(openapi=None)
    cancelled = asyncio.Event()

    @api.sse("/events", heartbeat=0.01)
    async def events(req, resp) -> AsyncIterator[Event]:
        try:
            await asyncio.Event().wait()
            if False:
                yield Event(id=1, message="never")
        finally:
            cancelled.set()

    async def main():
        async with AsyncTestClient(api) as client:
            async with asyncio.timeout(1):
                async with client.stream("GET", "/events") as response:
                    lines = response.aiter_lines()
                    assert (await anext(lines)).startswith(": keepalive")
        await asyncio.wait_for(cancelled.wait(), timeout=1)

    asyncio.run(main())


def test_typed_stream_supports_class_views_and_error_contracts():
    api = make_api()

    class Missing(BaseModel):
        detail: str

    @api.sse("/events", responses={404: Missing})
    class Events:
        async def on_get(self, req, resp) -> AsyncIterator[Event]:
            yield Event(id=1, message="ready")

    @api.ndjson("/missing", item_model=Event, responses={404: Missing})
    async def missing(req, resp):
        return Missing(detail="gone"), 404

    assert "data:" in api.requests.get("/events").text
    response = api.requests.get("/missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "gone"}


def test_typed_stream_composes_with_route_rate_limiting():
    api = make_api()
    limiter = RateLimiter(requests=1, period=60)

    @api.sse("/events")
    @limiter.limit
    async def events(req, resp) -> AsyncIterator[Event]:
        yield Event(id=1, message="ready")

    first = api.requests.get("/events")
    second = api.requests.get("/events")

    assert first.status_code == 200
    assert first.headers["x-ratelimit-remaining"] == "0"
    assert second.status_code == 429
    assert second.headers["content-type"].startswith("application/problem+json")
    assert second.json()["title"] == "Too Many Requests"


def test_final_no_content_response_closes_an_abandoned_stream_source():
    api = make_api()
    closed = False

    class Source:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return Event(id=1, message="never")

        async def aclose(self):
            nonlocal closed
            closed = True

    def suppress(req, resp):
        resp.no_content()

    @api.ndjson("/events", item_model=Event, after=suppress)
    async def events(req, resp):
        return Source()

    response = api.requests.get("/events")

    assert response.status_code == 204
    assert response.content == b""
    assert closed is True
