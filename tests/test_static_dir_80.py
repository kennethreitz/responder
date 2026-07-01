"""v8.0: ``API()`` never creates ``static_dir`` implicitly."""

import pytest

import responder


def _api(**kwargs):
    return responder.API(allowed_hosts=[";"], session_https_only=False, **kwargs)


def test_api_does_not_create_default_static_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _api()
    assert not (tmp_path / "static").exists()


def test_default_static_dir_mounted_when_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    static = tmp_path / "static"
    static.mkdir()
    (static / "style.css").write_text("body { color: red; }")

    api = _api()
    r = api.requests.get("/static/style.css")
    assert r.status_code == 200
    assert "color: red" in r.text


def test_default_static_dir_missing_gives_404_not_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api = _api()
    assert api.requests.get("/static/style.css").status_code == 404


def test_explicit_missing_static_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="static_dir"):
        _api(static_dir=str(tmp_path / "assets"))


def test_explicit_static_dir_that_is_a_file_raises(tmp_path):
    not_a_dir = tmp_path / "assets"
    not_a_dir.write_text("not a directory")
    with pytest.raises(FileNotFoundError, match="not a directory"):
        _api(static_dir=str(not_a_dir))


def test_explicit_existing_static_dir_serves(tmp_path):
    static = tmp_path / "assets"
    static.mkdir()
    (static / "a.txt").write_text("hello")

    api = _api(static_dir=str(static))
    assert api.requests.get("/static/a.txt").text == "hello"


def test_static_dir_none_still_disables_static(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api = _api(static_dir=None)

    @api.route("/")
    def home(req, resp):
        resp.text = "ok"

    assert api.requests.get("/").text == "ok"
    assert not (tmp_path / "static").exists()
    with pytest.raises(ValueError, match="static_dir is disabled"):
        api.add_route("/spa", static=True)


def test_spa_fallback_with_missing_default_static_dir(tmp_path, monkeypatch):
    # The default static_dir stays configured even when the directory is
    # absent, so the SPA fallback route still resolves (and 404s cleanly
    # instead of crashing).
    monkeypatch.chdir(tmp_path)
    api = _api()
    api.add_route("/", static=True)
    r = api.requests.get("/anything")
    assert r.status_code == 404
