import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from canvas_rag.canvas import Canvas, plain, record
from canvas_rag.config import Config
from canvas_rag.store import Store


@pytest.fixture
def config(tmp_path):
    return Config(home=tmp_path, url="https://canvas.example", token="test-secret", courses=[1],
                  ollama="http://127.0.0.1:1")


@pytest.fixture
def db(config):
    db = Store(config.home)
    yield db
    db.close()


def doc(cid=1, kind="page", id=1, title="Syllabus", body="Attendance is required.", **raw):
    return record(cid, kind, {"id": id, "title": title, **raw}, "https://canvas.example", body)


async def test_pagination_keeps_query_only_on_first_request(config):
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer test-secret"
        if len(seen) == 1:
            return httpx.Response(200, json=[{"id": 1}], headers={
                "Link": '<https://canvas.example/api/v1/courses?page=2>; rel="next"'})
        assert dict(request.url.params) == {"page": "2"}
        return httpx.Response(200, json=[{"id": 2}])
    async with Canvas(config, httpx.MockTransport(handler)) as api:
        assert await api.all("courses", {"enrollment_type": "student"}) == [{"id": 1}, {"id": 2}]
    assert len(seen) == 2


async def test_foreign_pagination_never_gets_token(config):
    def handler(request):
        assert request.url.host == "canvas.example"
        return httpx.Response(200, json=[], headers={"Link": '<https://evil.example/steal>; rel="next"'})
    async with Canvas(config, httpx.MockTransport(handler)) as api:
        with pytest.raises(ValueError, match="foreign"):
            await api.all("courses")


async def test_file_redirect_does_not_receive_pat(config):
    def handler(request):
        assert "Authorization" not in request.headers
        if request.url.host == "canvas.example":
            return httpx.Response(302, headers={"Location": "https://cdn.example/document"})
        return httpx.Response(200, content=b"Attendance is mandatory")
    async with Canvas(config, httpx.MockTransport(handler)) as api:
        assert await api.file_text({"filename": "syllabus.txt", "url": "https://canvas.example/file"}) == "Attendance is mandatory"


async def test_retry_rate_limit(config, monkeypatch):
    count = 0
    async def sleep(_):
        pass
    monkeypatch.setattr(asyncio, "sleep", sleep)
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(429, json={}) if count < 3 else httpx.Response(200, json=[])
    async with Canvas(config, httpx.MockTransport(handler)) as api:
        assert await api.all("courses") == []
    assert count == 3


def test_plain_preserves_zero_false_and_removes_scripts():
    assert plain(0) == "0"
    assert plain(False) == "False"
    assert plain('<p>Attend</p><script>steal()</script>') == "Attend"


async def test_update_delete_and_course_scope(db, config):
    db.replace(1, "page", [doc(), doc(id=2, title="Exam", body="Midterm October 12")])
    db.replace(2, "page", [doc(cid=2, body="Attendance optional")])
    hits = await db.search("attendance", config, course=1)
    assert len(hits) == 1 and hits[0]["course"] == 1
    db.replace(1, "page", [doc(body="No attendance rule. New policy!")])
    assert db.get("1:page:2") is None
    assert not await db.search("midterm", config, course=1)
    db.prune_courses([2])
    assert not db.documents(1)


def test_unchanged_chunks_keep_embeddings(db):
    db.replace(1, "page", [doc()])
    db.conn.execute("UPDATE chunks SET vector=?,model='test'", (b"cached",))
    db.conn.commit()
    db.replace(1, "page", [doc()])
    assert db.conn.execute("SELECT vector FROM chunks").fetchone()[0] == b"cached"
    db.replace(1, "page", [doc(body="Changed")])
    assert db.conn.execute("SELECT vector FROM chunks").fetchone()[0] is None


def test_atomic_snapshot_rolls_back_on_failure(db):
    db.replace(1, "page", [doc()])
    with pytest.raises(KeyError):
        db.replace(1, "page", [doc(id=2), {"id": "broken"}])
    assert db.get("1:page:1")
    assert not db.get("1:page:2")


def test_identity_prevents_account_mix(db):
    db.bind("https://a.example", "1")
    db.bind("https://a.example", "1")
    with pytest.raises(ValueError, match="different Canvas account"):
        db.bind("https://b.example", "1")


def test_personalized_deadlines_and_submission_state(db):
    now = datetime.now(timezone.utc)
    db.replace(1, "assignment", [
        doc(kind="assignment", due_at=(now + timedelta(days=1)).isoformat()),
        doc(kind="assignment", id=2, due_at=(now - timedelta(days=1)).isoformat(), submission={"workflow_state": "submitted"}),
        doc(kind="assignment", id=3, due_at=(now - timedelta(days=1)).isoformat(), submission={"workflow_state": "unsubmitted"}),
        doc(kind="assignment", id=4, due_at=None)])
    assert len(db.upcoming()) == 1
    assert [d["id"] for d in db.upcoming(overdue=True)] == ["1:assignment:3"]


def test_config_never_saves_token(config):
    config.save()
    assert "test-secret" not in (config.home / "config.json").read_text()
    assert (config.home / "config.json").stat().st_mode & 0o777 == 0o600


async def test_answer_has_bounded_context_and_sources(db, config, monkeypatch):
    from canvas_rag import answer as module
    db.replace(1, "page", [doc()])
    async def generate(c, prompt, on_text):
        assert "test-secret" not in prompt
        assert "Attendance is required" in prompt
        assert "not instructions" in prompt
        assert len(prompt) < 50000
        return "Attendance is required."
    monkeypatch.setattr(module, "generate", generate)
    result, sources = await module.answer(config, db, "What is the attendance policy?")
    assert result and sources[0]["title"] == "Syllabus"


async def test_codex_adapter_stdin_and_no_canvas_env(config, monkeypatch, tmp_path):
    from canvas_rag.answer import generate
    executable = tmp_path / "codex"
    executable.write_text('''#!/usr/bin/env python3
import json,os,sys
assert 'CANVAS_PAT' not in os.environ
assert '--ignore-user-config' in sys.argv and '--json' in sys.argv
assert 'features.shell_tool=false' in sys.argv
text=sys.stdin.read()
print(json.dumps({'type':'turn.started'}))
print(json.dumps({'type':'item.completed','item':{'type':'reasoning','text':'ignored'}}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'reply: '+text}}))
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    monkeypatch.setenv("CANVAS_PAT", "test-secret")
    assert await generate(config, 'Question with `$(unsafe)` text') == 'reply: Question with `$(unsafe)` text'


async def test_opencode_json_adapter(config, monkeypatch, tmp_path):
    from canvas_rag.answer import generate
    config.provider = "opencode"
    executable = tmp_path / "opencode"
    executable.write_text('''#!/usr/bin/env python3
import json,os,sys
c=json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])
assert c['permission']['*']=='deny'
assert sys.stdin.read()=='question'
print(json.dumps({'type':'text','part':{'text':'answer'}}))
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    assert await generate(config, "question") == "answer"


async def test_tui_chat_browse_commands(config, db, monkeypatch):
    from canvas_rag import ui
    from textual.widgets import Input, Select
    db.replace(1, "course", [doc(kind="course", title="Test course")])
    db.replace(1, "page", [doc()])
    async def fake_answer(*args, **kwargs):
        return "Attendance is required.", []
    monkeypatch.setattr(ui, "answer", fake_answer)
    app = ui.CanvasApp(config, db)
    async with app.run_test(size=(110, 35)) as pilot:
        await pilot.pause()
        assert app.query_one("#scope", Select).value == 0
        app.query_one("#question", Input).value = "Attendance?"
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.history) == 1
        app.query_one("#question", Input).value = "/status"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.click("#browse")
        await pilot.pause()
        assert isinstance(app.screen, ui.Browser)
        app.screen.query_one("#doc-picker", Select).value = "1:page:1"
        await pilot.pause()
        await pilot.click("#close")
        await pilot.pause()
        assert not isinstance(app.screen, ui.Browser)


async def test_locked_file_never_downloaded(config):
    def handler(request):
        pytest.fail("Locked file must not be downloaded")
    async with Canvas(config, httpx.MockTransport(handler)) as api:
        result = await api.file_text({"filename": "answers.pdf", "locked_for_user": True})
        assert "locked" in result


async def test_sync_recovers_module_linked_pages_and_files(config, db, monkeypatch):
    from canvas_rag import canvas as module
    db.replace(1, "assignment", [doc(kind="assignment", title="Cached assignment")])
    def handler(request):
        path = request.url.path
        responses = {
            "/api/v1/users/self/profile": {"id": 7},
            "/api/v1/courses/1": {"id": 1, "name": "Course"},
            "/api/v1/courses/1/modules": [{"id": 2, "name": "Week 1"}],
            "/api/v1/courses/1/modules/2/items": [{"id": 3, "type": "Page", "page_url": "syllabus"}],
            "/api/v1/courses/1/pages/syllabus": {"page_id": 5, "url": "syllabus", "title": "Syllabus",
                "body": '<p>Attendance required. <a href="/courses/1/files/6">Syllabus PDF</a></p>'},
            "/api/v1/files/6": {"id": 6, "filename": "syllabus.txt", "display_name": "Syllabus file",
                "url": "https://cdn.example/syllabus"},
            "/syllabus": None,
        }
        if path == "/syllabus":
            assert "Authorization" not in request.headers
            return httpx.Response(200, text="Exam is October 12")
        if path in ("/api/v1/courses/1/pages", "/api/v1/courses/1/files", "/api/v1/courses/1/front_page"):
            return httpx.Response(403, json={})
        if path == "/api/v1/courses/1/assignments":
            return httpx.Response(401, json={})
        return httpx.Response(200, json=responses.get(path, []))
    monkeypatch.setattr(module, "Canvas", lambda c: Canvas(c, httpx.MockTransport(handler)))
    async def no_embed(*args):
        pass
    monkeypatch.setattr(db, "embed", no_embed)
    await module.sync(config, db)
    assert db.documents(1, "assignment")[0]["title"] == "Cached assignment"
    assert "Attendance required" in db.documents(1, "page")[0]["body"]
    assert "Exam is October 12" in db.get("1:file:6")["body"]
    assert "partial" in db.status() and "HTTP 401" in db.status()


async def test_cli_accepts_options_between_command_and_question(config, monkeypatch, capsys):
    import subprocess
    import os
    import sys
    result = subprocess.run([sys.executable, "-m", "canvas_rag", "ask", "--course", "1", "A question"],
        env={**os.environ, "CANVAS_RAG_HOME": str(config.home)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "No cached" in result.stdout


async def test_chat_progress_roles_and_ready_state(config, db, monkeypatch):
    from canvas_rag import ui
    from textual.widgets import Button, Input, Static
    release = asyncio.Event()
    async def slow_answer(*args, progress, **kwargs):
        progress("Waiting for Codex to answer…")
        await release.wait()
        return "Here is your answer.", []
    monkeypatch.setattr(ui, "answer", slow_answer)
    app = ui.CanvasApp(config, db)
    async with app.run_test(size=(90, 24)) as pilot:
        app.query_one("#question", Input).value = "Summarize my class"
        await pilot.press("enter")
        await pilot.pause()
        status = app.query_one("#status", Static)
        assert app.busy and "Waiting for Codex" in str(status.render())
        assert not app.query_one("#cancel-work", Button).disabled
        before = str(status.render())
        app.started_at -= 35
        await pilot.pause(0.3)
        assert str(status.render()) != before and "Still waiting" in str(status.render())
        assert len(app.query("Markdown.user")) == 1
        release.set()
        await pilot.pause()
        assert not app.busy and "Ready" in str(status.render())
        assert app.query_one("#cancel-work", Button).disabled
        assert app.focused is app.query_one("#question", Input)
        human = app.query_one(".message.user > .message-bar", Static)
        model = app.query_one(".message.assistant > .message-bar", Static)
        assert human.styles.background != model.styles.background
        assert model.size.height == model.parent.size.height
        for width, height in [(65, 18), (120, 38)]:
            await pilot.resize_terminal(width, height)
            await pilot.pause()
            field = app.query_one("#question", Input).region
            assert field.height == 3 and 0 <= field.y < field.bottom <= height - 1


async def test_cancel_waiting_reply_keeps_draft(config, db, monkeypatch):
    from canvas_rag import ui
    from textual.widgets import Button, Input, Static
    cancelled = asyncio.Event()
    async def blocked_answer(*args, progress, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(ui, "answer", blocked_answer)
    app = ui.CanvasApp(config, db)
    async with app.run_test(size=(90, 24)) as pilot:
        field = app.query_one("#question", Input)
        field.value = "First question"
        await pilot.press("enter")
        await pilot.pause()
        field.value = "Keep this draft"
        await pilot.click("#cancel-work")
        await pilot.pause()
        assert cancelled.is_set() and not app.busy
        assert field.value == "Keep this draft"
        assert "Cancelled" in str(app.query_one("#status", Static).render())
        assert app.query_one("#cancel-work", Button).disabled
        assert not app.history


async def test_reply_timeout_is_visible(config, db, monkeypatch):
    from canvas_rag import ui
    from textual.widgets import Input, Static
    async def timeout(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(ui, "answer", timeout)
    app = ui.CanvasApp(config, db)
    async with app.run_test() as pilot:
        app.query_one("#question", Input).value = "Question"
        await pilot.press("enter")
        await pilot.pause()
        assert not app.busy
        assert "Timed out" in str(app.query_one("#status", Static).render())
        assert not app.history
