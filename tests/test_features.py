import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from textual.widgets import Markdown

from canvas_rag import canvas as canvas_module
from canvas_rag.answer import generate
from canvas_rag.canvas import Canvas, record, sync
from canvas_rag.config import Config
from canvas_rag.store import STALE_HOURS, Store

BASE = "https://canvas.example"


@pytest.fixture
def config(tmp_path):
    return Config(home=tmp_path, url=BASE, token="test-secret", courses=[1, 2], ollama="http://127.0.0.1:1")


@pytest.fixture
def db(config):
    db = Store(config.home)
    yield db
    db.close()


def assignment(id, name, due, cid=1):
    return record(cid, "assignment", {"id": id, "name": name, "due_at": due,
                                      "html_url": f"{BASE}/courses/{cid}/assignments/{id}"}, BASE)


def synced(db, course, kind, records):
    """A sync section: replace then record coverage, exactly as canvas.sync does."""
    db.replace(course, kind, records)
    db.coverage(course, kind, "ok", f"{len(records)} records")


def test_first_sync_logs_nothing_but_later_syncs_log_new_changed_removed(db):
    synced(db, 1, "course", [record(1, "course", {"id": 1, "name": "Astronomy"}, BASE, "")])
    synced(db, 1, "assignment", [assignment(1, "HW1", "2026-10-01T21:00:00Z"), assignment(2, "HW2", None)])
    synced(db, 1, "grade", [record(1, "grade", {"id": 9, "grades": {"current_score": 89}}, BASE)])
    assert db.changes() == []
    synced(db, 1, "assignment", [assignment(1, "HW1 revised", "2026-10-03T21:00:00Z"),
                                 assignment(3, "Lab", "2026-10-09T12:00:00Z")])
    synced(db, 1, "grade", [record(1, "grade", {"id": 9, "grades": {"current_score": 92.456}}, BASE)])
    log = {(c["change"], c["kind"]): c for c in db.changes()}
    assert set(log) == {("changed", "assignment"), ("new", "assignment"), ("removed", "assignment"), ("changed", "grade")}
    changed = log[("changed", "assignment")]
    assert changed["title"] == "HW1 revised"
    assert "retitled from 'HW1'" in changed["detail"] and "deadline moved from" in changed["detail"]
    assert log[("removed", "assignment")]["title"] == "HW2" and log[("removed", "assignment")]["detail"] == ""
    assert log[("changed", "grade")]["title"] == "Astronomy"
    assert log[("changed", "grade")]["detail"] == "current score 89 → 92.46"
    assert db.changes(course=2) == []


def test_activity_only_changes_are_not_logged(db):
    synced(db, 1, "course", [record(1, "course", {"id": 1, "name": "Astronomy"}, BASE, "")])
    grade = {"id": 9, "grades": {"current_score": 90}, "last_activity_at": "2026-09-01T00:00:00Z", "total_activity_time": 10}
    module = {"id": 4, "name": "Week 1", "items": [{"id": 1, "title": "Reading", "completion_requirement": {"completed": False}}]}
    synced(db, 1, "grade", [record(1, "grade", grade, BASE)])
    synced(db, 1, "module", [record(1, "module", module, BASE)])
    grade = {**grade, "last_activity_at": "2026-09-02T00:00:00Z", "total_activity_time": 99}
    module["items"][0]["completion_requirement"] = {"completed": True}
    synced(db, 1, "grade", [record(1, "grade", grade, BASE)])
    synced(db, 1, "module", [record(1, "module", module, BASE)])
    assert db.changes() == []
    synced(db, 1, "grade", [record(1, "grade", {**grade, "grades": {"current_score": 95}}, BASE)])
    synced(db, 1, "module", [record(1, "module", {**module, "items": module["items"] + [{"id": 2, "title": "Quiz"}]}, BASE)])
    assert sorted((c["kind"], c["detail"]) for c in db.changes()) == [("grade", "current score 90 → 95"), ("module", "")]


def test_change_log_is_rolling_window_not_cleared_per_sync(db):
    db.change(1, "announcement", "new", "Old news")
    db.conn.execute("UPDATE changes SET at=?", ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),))
    db.conn.commit()
    db.change(1, "announcement", "new", "Fresh news")
    db.prune_changes()
    assert [c["title"] for c in db.changes()] == ["Fresh news"]
    # Another sync with nothing new must not hide it.
    synced(db, 1, "page", [])
    synced(db, 1, "page", [])
    assert [c["title"] for c in db.changes()] == ["Fresh news"]


def test_dashboard_uses_one_date_format_and_names(db):
    due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    synced(db, 1, "course", [record(1, "course", {"id": 1, "name": "Astronomy"}, BASE, "")])
    synced(db, 1, "assignment", [assignment(1, "HW1", due)])
    synced(db, 1, "grade", [record(1, "grade", {"id": 9, "grades": {"current_score": 91.5}}, BASE)])
    db.change(1, "announcement", "new", "Observatory visit")
    text = db.dashboard()
    stamp = Store.when(due)
    assert f"- {stamp} · [HW1]({BASE}/courses/1/assignments/1)" in text
    assert "T" not in stamp and "**Recent changes** (last 7 days; 1 recorded)" in text
    assert "New · announcement · Observatory visit" in text
    assert "- Astronomy: 91.5" in text
    assert db.dashboard(days=30).startswith("**Upcoming work** (next 30 days")
    assert Store.when(None) is None and Store.when("garbage") is None


def test_stale_uses_last_sync_and_falls_back_to_documents(db):
    assert not db.stale()
    synced(db, 1, "page", [record(1, "page", {"id": 1, "title": "P"}, BASE, "x")])
    assert not db.stale()
    db.conn.execute("UPDATE documents SET synced=?", ((datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS + 1)).isoformat(),))
    db.conn.commit()
    assert db.stale()
    db.mark_synced()
    assert not db.stale()


def test_digest_and_changes_cli(config, db, monkeypatch):
    synced(db, 1, "course", [record(1, "course", {"id": 1, "name": "Astronomy"}, BASE, "")])
    db.change(1, "announcement", "new", "Observatory visit")
    db.close()
    config.save()
    env = {**os.environ, "CANVAS_BUDDY_HOME": str(config.home)}
    out = subprocess.run([sys.executable, "-m", "canvas_rag", "digest", "--days", "3"], env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "next 3 days" in out.stdout and "Observatory visit" in out.stdout
    out = subprocess.run([sys.executable, "-m", "canvas_rag", "changes"], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "Observatory visit" in out.stdout
    out = subprocess.run([sys.executable, "-m", "canvas_rag", "sync", "--if-stale"], env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0 and "skipping sync" in out.stdout


def test_env_file_only_read_inside_checkout(tmp_path, monkeypatch):
    for name in ("CANVAS_PAT", "CANVAS_URL", "CANVAS_RAG_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CANVAS_BUDDY_HOME", str(tmp_path / "data"))
    stray = tmp_path / "some-project"
    stray.mkdir()
    (stray / ".env").write_text("CANVAS_URL=https://evil.example\nCANVAS_PAT=stolen\n")
    monkeypatch.chdir(stray)
    assert Config.load().url == "" and Config.load().token == ""
    checkout = tmp_path / "checkout"
    (checkout / "canvas_rag").mkdir(parents=True)
    (checkout / "canvas_rag" / "__init__.py").write_text("")
    (checkout / "pyproject.toml").write_text("[project]\nname='canvas-buddy'\n")
    (checkout / ".env").write_text("CANVAS_URL=https://school.example\nCANVAS_PAT=dev-token\n")
    monkeypatch.chdir(checkout)
    assert Config.load().url == "https://school.example" and Config.load().token == "dev-token"


async def test_ollama_streams_tokens(monkeypatch):
    lines = [json.dumps({"message": {"content": "Hel"}}), json.dumps({"message": {"content": "lo"}}),
             json.dumps({"message": {"content": ""}, "done": True})]
    original = httpx.AsyncClient
    def respond(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content="\n".join(lines).encode())
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    seen = []
    result = await generate(Config(provider="ollama", model="m"), "q", seen.append)
    assert result == "Hello" and seen == ["Hel", "Hello", "Hello"]


async def test_ollama_error_event_is_raised(monkeypatch):
    original = httpx.AsyncClient
    def respond(request):
        return httpx.Response(200, content=json.dumps({"error": "model 'm' not found"}).encode())
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(RuntimeError, match="not found"):
        await generate(Config(provider="ollama", model="m"), "q")


async def test_opencode_delivers_parts_as_they_arrive(config, monkeypatch, tmp_path):
    config.provider = "opencode"
    executable = tmp_path / "opencode"
    executable.write_text('''#!/usr/bin/env python3
import json,sys,time
sys.stdin.read()
print(json.dumps({'type':'text','part':{'text':'first'}}), flush=True)
time.sleep(0.3)
print(json.dumps({'type':'text','part':{'text':'second'}}), flush=True)
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    stamps = []
    result = await generate(config, "q", lambda text: stamps.append((asyncio.get_event_loop().time(), text)))
    assert result == "first\nsecond"
    assert [t for _, t in stamps] == ["first", "first\nsecond"]
    assert stamps[1][0] - stamps[0][0] >= 0.2


async def test_codex_error_event_kills_process(config, monkeypatch, tmp_path):
    executable = tmp_path / "codex"
    executable.write_text('''#!/usr/bin/env python3
import json,sys,time
sys.stdin.read()
print(json.dumps({'type':'error','message':'login required'}), flush=True)
time.sleep(30)
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    start = asyncio.get_event_loop().time()
    with pytest.raises(RuntimeError, match="login required"):
        await generate(config, "q")
    assert asyncio.get_event_loop().time() - start < 10


async def settles(pilot, condition, seconds=3.0):
    """Poll instead of a fixed sleep: worker scheduling under a loaded test run is not deterministic."""
    for _ in range(int(seconds / 0.05)):
        if condition():
            return True
        await pilot.pause(0.05)
    return condition()


async def test_tui_streams_reply_into_one_bubble(config, db, monkeypatch):
    from canvas_rag import ui
    from textual.widgets import Input
    synced(db, 1, "page", [record(1, "page", {"id": 1, "title": "Syllabus"}, BASE, "Attendance is required.")])
    release = asyncio.Event()
    async def streaming_answer(*args, on_text, **kwargs):
        on_text("Attendance")
        await release.wait()
        on_text("Attendance is required")
        await asyncio.sleep(0.3)
        return "Attendance is required.", []
    monkeypatch.setattr(ui, "answer", streaming_answer)
    app = ui.CanvasApp(config, db)
    async with app.run_test(size=(100, 30)) as pilot:
        app.query_one("#question", Input).value = "Attendance?"
        await pilot.press("enter")
        assert await settles(pilot, lambda: list(app.query(Markdown))[-1]._markdown.endswith("Attendance"))
        assert app.busy
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        replies = [w for w in app.query(Markdown) if "assistant" in w.classes]
        assert len(replies) == 1 and replies[0]._markdown.endswith("Attendance is required.")
        assert app.history == [("Attendance?", "Attendance is required.")]


async def test_tui_cancelled_reply_removes_placeholder(config, db, monkeypatch):
    from canvas_rag import ui
    from textual.widgets import Input
    synced(db, 1, "page", [record(1, "page", {"id": 1, "title": "Syllabus"}, BASE, "x")])
    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(ui, "answer", blocked)
    app = ui.CanvasApp(config, db)
    async with app.run_test() as pilot:
        app.query_one("#question", Input).value = "Q"
        await pilot.press("enter")
        assert await settles(pilot, lambda: any("Thinking" in w._markdown for w in app.query(Markdown)))
        await pilot.press("escape")
        assert await settles(pilot, lambda: not app.busy)
        assert not any("Thinking" in w._markdown for w in app.query(Markdown))
        assert not app.query(".message.assistant")


@pytest.mark.parametrize("hours_old,demo,expected", [(STALE_HOURS + 1, False, True), (1, False, False), (STALE_HOURS + 1, True, False)])
async def test_stale_cache_syncs_on_launch(config, db, monkeypatch, hours_old, demo, expected):
    from canvas_rag import ui
    synced(db, 1, "course", [record(1, "course", {"id": 1, "name": "Astronomy"}, BASE, "")])
    db.conn.execute("INSERT OR REPLACE INTO meta VALUES('last_sync',?)",
                    ((datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat(),))
    db.conn.commit()
    app = ui.CanvasApp(config, db, demo=demo)
    requested = []
    monkeypatch.setattr(app, "run_sync", lambda: requested.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert bool(requested) is expected
        assert any("Home · Your dashboard" in w._markdown for w in app.query(Markdown))


async def test_first_run_has_no_empty_dashboard(tmp_path, monkeypatch):
    from canvas_rag import ui
    monkeypatch.setenv("CANVAS_BUDDY_HOME", str(tmp_path))
    for name in ("CANVAS_PAT", "CANVAS_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    c = Config.load()
    db = Store(c.home)
    app = ui.CanvasApp(c, db)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ui.Setup)
            assert not any("Home · Your dashboard" in w._markdown for w in app.query(Markdown))
    finally:
        db.close()


async def test_courses_sync_concurrently_and_in_order(config, db, monkeypatch):
    in_flight, peak, order = 0, 0, {1: [], 2: []}
    async def handler(request):
        nonlocal in_flight, peak
        path = request.url.path
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        for cid in (1, 2):
            if path.startswith(f"/api/v1/courses/{cid}"):
                order[cid].append(path.split(f"/courses/{cid}", 1)[1])
        if path == "/api/v1/users/self/profile":
            return httpx.Response(200, json={"id": 7})
        if path in ("/api/v1/courses/1", "/api/v1/courses/2"):
            return httpx.Response(200, json={"id": int(path[-1]), "name": f"Course {path[-1]}"})
        if path.endswith("/front_page"):
            return httpx.Response(404, json={})
        if path.endswith("/todo"):
            return httpx.Response(200, json=[{"type": "submitting", "assignment": {"id": 5, "name": "Essay", "due_at": "2026-10-01T00:00:00Z"},
                                              "html_url": f"{BASE}/courses/1/assignments/5"}])
        return httpx.Response(200, json=[])
    monkeypatch.setattr(canvas_module, "Canvas", lambda c: Canvas(c, httpx.MockTransport(handler)))
    async def no_embed(*args):
        pass
    monkeypatch.setattr(db, "embed", no_embed)
    db.change(1, "announcement", "new", "Kept")
    await sync(config, db)
    assert peak >= 2
    for cid in (1, 2):
        assert order[cid].index("/modules") < order[cid].index("/front_page")
        assert db.documents(cid, "course")[0]["title"] == f"Course {cid}"
    todo = db.documents(1, "todo")
    assert todo and todo[0]["title"] == "Essay" and todo[0]["id"] == "1:todo:5"
    assert "todo (submitting) Essay | due=2026-10-01T00:00:00Z" in db.facts(1)
    assert not db.stale() and [c["title"] for c in db.changes()] == ["Kept"]


async def test_failed_sync_keeps_change_log_and_last_sync(config, db, monkeypatch):
    db.change(1, "announcement", "new", "Kept")
    monkeypatch.setattr(canvas_module, "Canvas", lambda c: Canvas(c, httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(RuntimeError, match="Token expired"):
        await sync(config, db)
    assert [c["title"] for c in db.changes()] == ["Kept"]
    assert db.conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone() is None


async def test_one_course_failure_cancels_the_others_before_client_closes(config, db, monkeypatch):
    progress = []
    async def handler(request):
        path = request.url.path
        await asyncio.sleep(0.01)
        if path == "/api/v1/users/self/profile":
            return httpx.Response(200, json={"id": 7})
        if path == "/api/v1/courses/1/modules":
            return httpx.Response(200, json=[{"name": "no id field"}])
        if path in ("/api/v1/courses/1", "/api/v1/courses/2"):
            return httpx.Response(200, json={"id": int(path[-1]), "name": "C"})
        return httpx.Response(200, json=[])
    monkeypatch.setattr(canvas_module, "Canvas", lambda c: Canvas(c, httpx.MockTransport(handler)))
    with pytest.raises(KeyError):
        await sync(config, db, progress.append)
    seen = len(progress)
    await asyncio.sleep(0.2)
    assert len(progress) == seen, "sibling course kept running after the sync failed"
    assert not [r for r in db.conn.execute("SELECT detail FROM coverage WHERE detail LIKE '%closed%'")]


async def test_failed_first_fetch_does_not_flood_log_and_prune_drops_changes(db):
    db.coverage(1, "assignment", "error", "Canvas HTTP 503")
    synced(db, 1, "assignment", [assignment(1, "HW1", None), assignment(2, "HW2", None)])
    assert db.changes() == []
    synced(db, 1, "assignment", [assignment(1, "HW1", None), assignment(2, "HW2", None), assignment(3, "HW3", None)])
    assert [c["title"] for c in db.changes()] == ["HW3"]
    db.prune_courses([2])
    assert db.changes() == []


async def test_oversized_agent_message_line_is_read_and_child_reaped(config, monkeypatch, tmp_path):
    executable = tmp_path / "codex"
    executable.write_text('''#!/usr/bin/env python3
import json,sys
sys.stdin.read()
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'x'*200000}}))
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    assert len(await generate(config, "q")) == 200000


async def test_child_that_exits_without_reading_reports_stderr(config, monkeypatch, tmp_path):
    executable = tmp_path / "codex"
    executable.write_text('''#!/usr/bin/env python3
import sys
sys.stderr.write("not logged in\\n")
sys.exit(1)
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    with pytest.raises(RuntimeError, match="not logged in"):
        await generate(config, "q" * 300000)


async def test_callback_failure_kills_child(config, monkeypatch, tmp_path):
    executable = tmp_path / "codex"
    executable.write_text('''#!/usr/bin/env python3
import json,sys,time
sys.stdin.read()
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'hi'}}), flush=True)
time.sleep(30)
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    def explode(text):
        raise ValueError("ui broke")
    start = asyncio.get_event_loop().time()
    with pytest.raises(ValueError, match="ui broke"):
        await generate(config, "q", explode)
    assert asyncio.get_event_loop().time() - start < 10


async def test_second_escape_does_not_interrupt_cancellation(config, db, monkeypatch):
    from canvas_rag import ui
    from textual.widgets import Input
    synced(db, 1, "page", [record(1, "page", {"id": 1, "title": "Syllabus"}, BASE, "x")])
    cancels = []
    async def slow_cleanup(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancels.append(1)
            await asyncio.sleep(0.3)  # the kill sequence; a second cancel here would skip it
            cancels.append(2)
            raise
    monkeypatch.setattr(ui, "answer", slow_cleanup)
    app = ui.CanvasApp(config, db)
    async with app.run_test() as pilot:
        app.query_one("#question", Input).value = "Q"
        await pilot.press("enter")
        assert await settles(pilot, lambda: app.busy)
        await pilot.press("escape")
        await pilot.pause(0.05)
        await pilot.press("escape")
        assert await settles(pilot, lambda: not app.busy)
        assert cancels == [1, 2]
