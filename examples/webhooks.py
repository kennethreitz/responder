"""A signed webhook receiver that verifies the raw request body.

Run it:

    WEBHOOK_SECRET=development-secret responder run examples/webhooks.py

Try it with Python:

    python - <<'PY'
    import hmac, hashlib, json, requests

    body = json.dumps({"type": "invoice.paid", "data": {"invoice": "in_123"}})
    signature = "sha256=" + hmac.new(
        b"development-secret", body.encode(), hashlib.sha256
    ).hexdigest()
    response = requests.post(
        "http://127.0.0.1:5042/webhooks/events",
        data=body,
        headers={"Content-Type": "application/json", "X-Signature": signature},
    )
    print(response.json())
    PY
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

import responder


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def sign_payload(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = sign_payload(body, secret)
    return hmac.compare_digest(expected, signature)


class WebhookIn(BaseModel):
    type: str = Field(min_length=1, examples=["invoice.paid"])
    data: dict[str, Any] = Field(default_factory=dict, examples=[{"invoice": "in_123"}])


class WebhookEvent(WebhookIn):
    id: int
    received_at: datetime


class WebhookAccepted(BaseModel):
    accepted: bool
    event: WebhookEvent


EVENT_EXAMPLE = {
    "id": 1,
    "type": "invoice.paid",
    "data": {"invoice": "in_123"},
    "received_at": "2026-06-30T12:00:00Z",
}


@dataclass
class WebhookStore:
    events: list[WebhookEvent] = field(default_factory=list)

    def append(self, incoming: WebhookIn) -> WebhookEvent:
        event = WebhookEvent(
            id=len(self.events) + 1,
            received_at=_now(),
            **incoming.model_dump(),
        )
        self.events.append(event)
        return event.model_copy(deep=True)

    def list(self) -> list[WebhookEvent]:
        return [event.model_copy(deep=True) for event in self.events]


def create_api(
    *,
    store: WebhookStore | None = None,
    secret: str | None = None,
) -> responder.API:
    store = store or WebhookStore()
    secret = secret or os.environ.get("WEBHOOK_SECRET", "development-secret")  # noqa: S105

    api = responder.API(
        title="Signed Webhooks API",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
    )

    @api.get("/", include_in_schema=False)
    def index(req, resp):
        resp.media = {
            "name": "Signed Webhooks API",
            "webhook": "/webhooks/events",
            "events": "/events",
            "docs": "/docs",
        }

    @api.get(
        "/events",
        operation_id="list_webhook_events",
        tags=["webhooks"],
        summary="List accepted events",
        response_model=list[WebhookEvent],
        examples={"accepted": {"value": [EVENT_EXAMPLE]}},
    )
    def list_events(req, resp):
        resp.media = store.list()

    @api.post(
        "/webhooks/events",
        operation_id="receive_webhook_event",
        tags=["webhooks"],
        summary="Receive a signed webhook event",
        response_model=WebhookAccepted,
        responses={
            202: "Webhook accepted",
            400: "Invalid payload",
            401: "Invalid signature",
        },
        response_examples={
            202: {
                "accepted": {
                    "value": {"accepted": True, "event": EVENT_EXAMPLE},
                }
            }
        },
    )
    async def receive_webhook(
        req,
        resp,
        *,
        signature: str = responder.Header("", alias="X-Signature"),
    ):
        body = await req.content
        if not verify_signature(body, signature, secret):
            resp.problem(
                401,
                "Webhook signature did not match the request body.",
                type="https://responder.example/problems/invalid-webhook-signature",
            )
            return

        try:
            incoming = WebhookIn.model_validate_json(body)
        except ValidationError as exc:
            resp.problem(
                400,
                "Webhook payload must be valid JSON with a non-empty type.",
                type="https://responder.example/problems/invalid-webhook-payload",
                errors=exc.errors(),
            )
            return

        event = store.append(incoming)
        resp.status_code = 202
        resp.media = WebhookAccepted(accepted=True, event=event)

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
