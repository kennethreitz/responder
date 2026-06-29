import types

import pytest

import responder


def test_serve_defaults_to_uvicorn(monkeypatch):
    calls = []
    api = responder.API(allowed_hosts=[";"])

    def run(app, **kwargs):
        calls.append((app, kwargs))

    monkeypatch.setattr("responder.api.uvicorn.run", run)

    api.serve(address="127.0.0.1", port=8000)

    assert calls == [(api, {"host": "127.0.0.1", "port": 8000})]


def test_serve_rejects_unknown_server():
    api = responder.API(allowed_hosts=[";"])

    with pytest.raises(ValueError, match="Unsupported server"):
        api.serve(server="gunicorn")


def test_serve_granian_missing_extra(monkeypatch):
    api = responder.API(allowed_hosts=[";"])

    def import_module(name):
        if name.startswith("granian"):
            raise ImportError(name)
        raise AssertionError(name)

    monkeypatch.setattr("responder.api.importlib.import_module", import_module)

    with pytest.raises(RuntimeError, match="responder\\[server\\]"):
        api.serve(server="granian")


def test_serve_granian_uses_embedded_asgi_server(monkeypatch):
    calls = []
    api = responder.API(allowed_hosts=[";"])

    class FakeServer:
        def __init__(self, app, **kwargs):
            calls.append(("init", app, kwargs))

        async def serve(self):
            calls.append(("serve",))

    def import_module(name):
        if name == "granian.server.embed":
            return types.SimpleNamespace(Server=FakeServer)
        if name == "granian.constants":
            return types.SimpleNamespace(
                Interfaces=types.SimpleNamespace(ASGI="asgi")
            )
        raise AssertionError(name)

    monkeypatch.setattr("responder.api.importlib.import_module", import_module)

    api.serve(
        address="127.0.0.1",
        port=8000,
        debug=True,
        server="granian",
        log_access=True,
    )

    assert calls == [
        (
            "init",
            api,
            {
                "address": "127.0.0.1",
                "port": 8000,
                "interface": "asgi",
                "log_level": "debug",
                "log_access": True,
            },
        ),
        ("serve",),
    ]


def test_serve_granian_rejects_multiple_workers():
    api = responder.API(allowed_hosts=[";"])

    with pytest.raises(ValueError, match="multiple workers"):
        api.serve(server="granian", workers=2)
