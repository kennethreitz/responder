"""``responder routes`` and the route table it prints."""

import json
import logging
import sys

import pytest
from pydantic import BaseModel

from responder import Depends, Form
from responder.ext.cli import cli
from responder.ext.routeinfo import describe_routes, format_routes


class ItemIn(BaseModel):
    name: str


class ItemOut(BaseModel):
    id: int
    name: str


class Search(BaseModel):
    q: str


def get_db():
    return {"ready": True}


def test_describe_routes_lists_models_and_dependencies(api):
    @api.dependency()
    def db():
        return []

    @api.post("/items", dependencies=[Depends(get_db)])
    def create_item(req, resp, *, item: ItemIn, db) -> ItemOut:
        return ItemOut(id=1, name=item.name)

    @api.get("/search", params_model=Search)
    def search(req, resp):
        resp.media = {"q": "x"}

    @api.route("/free")
    def free(req, resp):
        resp.text = "free"

    @api.websocket_route("/ws/{room}")
    async def chat(ws, *, room):
        await ws.accept()

    @api.get("/plain")
    def plain(req, resp) -> str:
        return "hi"

    rows = {row["path"]: row for row in describe_routes(api)}

    assert rows["/items"]["methods"] == ["POST"]
    assert rows["/items"]["endpoint"] == "create_item"
    assert rows["/items"]["request_model"] == "ItemIn"
    assert rows["/items"]["response_model"] == "ItemOut"
    assert rows["/items"]["dependencies"] == ["get_db", "db"]

    assert rows["/search"]["request_model"] == "Search"
    assert rows["/search"]["response_model"] is None
    assert rows["/free"]["methods"] == ["*"]
    assert rows["/ws/{room}"]["methods"] == ["WEBSOCKET"]
    assert rows["/ws/{room}"]["endpoint"] == "chat"
    assert rows["/plain"]["response_model"] is None

    table = format_routes(describe_routes(api))
    assert table.startswith("Methods")
    assert "/items" in table
    assert "ItemIn" in table
    assert "get_db, db" in table
    assert "WEBSOCKET" in table
    assert "—" in table
    assert format_routes([]).endswith("(no routes)\n")


def test_describe_routes_splits_class_and_stream_models(api):
    @api.route("/widgets")
    class Widget:
        def on_get(self, req, resp) -> ItemOut:
            return ItemOut(id=1, name="Ada")

        def on_post(self, req, resp, *, item: ItemIn) -> Search:
            return Search(q=item.name)

    @api.route("/any")
    class AnyView:
        def on_request(self, req, resp):
            resp.text = "any"

    @api.sse("/events", event_model=ItemOut)
    async def events(req, resp):
        yield {"id": 1, "name": "Ada"}

    @api.post("/find")
    def find(req, resp, *, search: Search = Form(...)) -> ItemOut | None:
        return None

    rows = {row["path"]: row for row in describe_routes(api)}
    assert rows["/widgets"]["methods"] == ["GET", "POST"]
    assert rows["/widgets"]["request_model"] == "ItemIn"
    assert rows["/widgets"]["response_model"] == "GET:ItemOut, POST:Search"
    assert rows["/any"]["methods"] == ["*"]
    assert rows["/events"]["response_model"] == "ItemOut (sse)"
    assert rows["/find"]["request_model"] == "Search"
    assert rows["/find"]["response_model"] == "ItemOut | None"


def test_routes_command_table_and_json(tmp_path, monkeypatch, capsys):
    app = tmp_path / "shop.py"
    app.write_text(
        "import responder\n"
        "from pydantic import BaseModel\n"
        "\n"
        "class ItemOut(BaseModel):\n"
        "    id: int\n"
        "    name: str\n"
        "\n"
        "api = responder.API()\n"
        "\n"
        "@api.dependency()\n"
        "def db():\n"
        "    return []\n"
        "\n"
        '@api.get("/items/{item_id:int}")\n'
        "def get_item(req, resp, *, item_id: int, db) -> ItemOut:\n"
        '    resp.media = {"id": item_id, "name": "Ada"}\n'
    )

    monkeypatch.setattr(sys, "argv", ["responder", "routes", str(app)])
    cli()
    table = capsys.readouterr().out
    assert "Methods" in table and "Dependencies" in table
    assert "GET" in table
    assert "/items/{item_id}" in table
    assert "get_item" in table
    assert "ItemOut" in table
    assert "db" in table

    monkeypatch.setattr(sys, "argv", ["responder", "routes", "--json", str(app)])
    cli()
    payload = json.loads(capsys.readouterr().out)
    row = next(item for item in payload if item["endpoint"] == "get_item")
    assert row["methods"] == ["GET"]
    assert row["path"] == "/items/{item_id}"
    assert row["request_model"] is None
    assert row["response_model"] == "ItemOut"
    assert row["dependencies"] == ["db"]


def test_routes_command_rejects_a_non_api(tmp_path, monkeypatch, caplog):
    app = tmp_path / "nope.py"
    app.write_text("def api(scope, receive, send):\n    return None\n")
    monkeypatch.setattr(sys, "argv", ["responder", "routes", str(app)])
    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as exc:
            cli()
    assert exc.value.code == 1
    assert "not a Responder API" in caplog.text


def test_routes_help_lists_the_command(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["responder", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli()
    assert exc.value.code in (0, None)
    assert "responder routes" in capsys.readouterr().out
