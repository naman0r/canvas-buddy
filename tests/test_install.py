import struct
import subprocess
import sys
from importlib.metadata import version

import httpx
import pytest

from canvas_rag.config import Config
from canvas_rag.store import Store
from canvas_rag.canvas import record


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("CANVAS_PAT", "CANVAS_URL", "CANVAS_RAG_HOME", "CANVAS_RAG_PROVIDER", "CANVAS_RAG_MODEL",
                 "CANVAS_RAG_EMBED_MODEL", "OLLAMA_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CANVAS_BUDDY_HOME", str(tmp_path / "data"))
    return Config.load()


def test_saved_credentials_work_outside_checkout(fresh, monkeypatch, tmp_path):
    fresh.url, fresh.token = "https://canvas.example", "private-token"
    fresh.courses = [1]
    fresh.save_token()
    fresh.save()
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    assert Config.load().token == "private-token"
    assert Config.load().courses == [1]
    assert (fresh.home / "credentials.json").stat().st_mode & 0o777 == 0o600
    assert fresh.home.stat().st_mode & 0o777 == 0o700
    assert "private-token" not in (fresh.home / "config.json").read_text()


def test_saved_token_not_sent_to_changed_host(fresh, monkeypatch):
    fresh.url, fresh.token = "https://canvas.example", "private-token"
    fresh.save_token()
    fresh.save()
    monkeypatch.setenv("CANVAS_URL", "https://different.example")
    assert Config.load().token == ""
    monkeypatch.setenv("CANVAS_PAT", "explicit-new-token")
    assert Config.load().token == "explicit-new-token"


@pytest.mark.parametrize("content", ['{"courses": "bad"}', '{"provider":"invalid"}', '{"url":false}', '{broken'])
def test_bad_config_has_actionable_error(fresh, content):
    (fresh.home / "config.json").write_text(content)
    with pytest.raises(ValueError):
        Config.load()


async def test_keyword_mode_makes_no_network_requests(fresh, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Keyword-only search must not contact Ollama")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    db = Store(fresh.home)
    try:
        db.replace(1, "page", [record(1, "page", {"id": 1, "title": "Syllabus"}, "https://canvas.example", "Attendance required")])
        assert (await db.search("attendance", fresh))[0]["title"] == "Syllabus"
        await db.embed(fresh)
        assert "Disabled" in db.status()
    finally:
        db.close()


async def test_vectors_remain_compatible_without_numpy(fresh, monkeypatch):
    fresh.embed_model = "nomic-embed-text"
    db = Store(fresh.home)
    db.replace(1, "page", [record(1, "page", {"id": 1, "title": "Policy"}, "https://canvas.example", "Be present")])
    with db.conn:
        db.conn.execute("UPDATE chunks SET vector=?,model=?", (struct.pack("ff", 1, 0), fresh.embed_model))
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"embeddings": [[1, 0]]}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    try:
        assert (await db.search("attendance", fresh))[0]["title"] == "Policy"
    finally:
        db.close()


async def test_first_run_setup_validates_then_persists(fresh, monkeypatch):
    from canvas_rag import ui
    from textual.widgets import Input, SelectionList
    class FakeCanvas:
        def __init__(self, config):
            config.validate_canvas()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def one(self, path):
            return {"id": 42}
        async def courses(self):
            return [{"id": 1, "name": "Example class"}]
    monkeypatch.setattr(ui, "Canvas", FakeCanvas)
    db = Store(fresh.home)
    app = ui.CanvasApp(fresh, db)
    sync_requested = []
    monkeypatch.setattr(app, "run_sync", lambda: sync_requested.append(True))
    try:
        async with app.run_test(size=(90, 30)) as pilot:
            assert isinstance(app.screen, ui.Setup)
            app.screen.query_one("#url", Input).value = "https://canvas.example"
            app.screen.query_one("#token", Input).value = "private-token"
            await pilot.click("#load")
            await pilot.pause()
            assert fresh.token == "" and not (fresh.home / "credentials.json").exists()
            app.screen.query_one(SelectionList).select(1)
            await pilot.click("#save")
            await pilot.pause()
            assert sync_requested and Config.load().token == "private-token"
            assert Config.load().courses == [1]
    finally:
        db.close()


async def test_setup_cancel_does_not_change_configuration(fresh):
    from canvas_rag.ui import CanvasApp, Setup
    from textual.widgets import Input
    db = Store(fresh.home)
    app = CanvasApp(fresh, db)
    try:
        async with app.run_test() as pilot:
            assert isinstance(app.screen, Setup)
            app.screen.query_one("#token", Input).value = "do-not-save"
            await pilot.press("escape")
            await pilot.pause()
            assert not (fresh.home / "credentials.json").exists()
            assert fresh.token == ""
    finally:
        db.close()


def test_installed_cli_help_and_self_test_without_credentials(fresh):
    for args, expected in [(('--version',), f"Canvas Buddy {version('canvas-buddy')}"),
                           (('self-test',), 'Self-test passed'), (('doctor',), 'canvas_pat_set')]:
        result = subprocess.run([sys.executable, '-m', 'canvas_rag', *args], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout
    assert not (fresh.home / 'credentials.json').exists()
