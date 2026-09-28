import os
import subprocess
import sys

import httpx
import pytest
from textual.widgets import Button, OptionList, Static, TextArea

from canvas_rag import ui
from canvas_rag.canvas import record
from canvas_rag.config import Config
from canvas_rag.context import Context
from canvas_rag.library import Library
from canvas_rag.planner import Planner
from canvas_rag.sites import fetch_site, google_export, sheet_text, sync_sites, within
from canvas_rag.store import Store

BASE = "https://canvas.example"
SITE = "https://prof.example/teaching/db/fall/"


@pytest.fixture
def config(tmp_path):
    return Config(home=tmp_path, url=BASE, token="canvas-secret", courses=[1], embed_model="")


@pytest.fixture
def db(config):
    db = Store(config.home)
    db.bind(BASE, "7")
    db.replace(1, "course", [record(1, "course", {"id": 1, "name": "DS4300 13913 Databases SEC 01 Fall 2026"}, BASE)])
    db.replace(1, "announcement", [record(1, "announcement", {"id": 2, "title": "Welcome", "message":
        f'<a href="{SITE}">site</a> <a href="{SITE}">again</a> <a href="https://other.example/x">x</a> '
        f'<a href="{BASE}/courses/1/files/3">file</a> <a href="https://cdn.example/logo.png">logo</a>'}, BASE)])
    yield db
    db.close()


PAGES = {
    SITE: '<html><title>DB Fall</title><nav>Home | Blog</nav><h2>Exams</h2><p>Exam 1 is Oct 19.</p>'
          '<a href="schedule">Schedule</a><a href="/blog/post">Blog</a><a href="notes.pdf">Notes</a>'
          '<a href="https://docs.google.com/spreadsheets/d/SHEET/edit">Office hours</a>'
          '<a href="https://docs.google.com/document/d/PRIVATE/edit">Private doc</a>'
          '<a href="away">Away</a></html>',
    SITE + "schedule": "<html><title>Schedule</title><p>Week 7: distributed systems.</p></html>",
    SITE + "away": httpx.Response(302, headers={"location": "https://evil.example/"}),
    "https://docs.google.com/spreadsheets/d/SHEET/export?format=csv":
        httpx.Response(200, text="Hours,,\n,Monday,Tuesday\n10 AM,Erika,\n", headers={"content-type": "text/csv"}),
    "https://docs.google.com/document/d/PRIVATE/export?format=txt":
        httpx.Response(200, text="<html>Sign in</html>", headers={"content-type": "text/html"}),
}


def serve(requests):
    def handler(request):
        requests.append(request)
        page = PAGES.get(str(request.url))
        if isinstance(page, httpx.Response):
            return page
        if page is None:
            return httpx.Response(404)
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})
    return httpx.MockTransport(handler)


def test_context_round_trip_dedupes_and_survives_deselection(db):
    db.set_context(1, notes="  48-hour extension on homework  ", sites=[SITE, SITE])
    db.set_context(1, sites=[SITE, "https://b.example/"])
    db.prune_courses([])
    assert db.context() == {1: {"notes": "48-hour extension on homework", "sites": [SITE, "https://b.example/"]}}


def test_suggestions_are_external_course_links_most_linked_first(db):
    assert db.suggested_sites(1) == [SITE, "https://other.example/x"]
    db.set_context(1, sites=[SITE])
    assert db.suggested_sites(1) == ["https://other.example/x"]


def test_scope_google_exports_and_sheets():
    assert within(SITE + "schedule", SITE) and within(SITE + "a/b.pdf", SITE)
    assert not within("https://prof.example/blog/post", SITE) and not within("https://x@prof.example/teaching/db/fall/", SITE)
    assert within("https://h.example/course/week1", "https://h.example/course/index.html")
    assert google_export("https://docs.google.com/presentation/d/AB-c_1/edit?usp=sharing") == \
        "https://docs.google.com/presentation/d/AB-c_1/export/txt"
    assert google_export("https://docs.google.com/forms/d/x/viewform") is None
    assert sheet_text("Hours,,\n,Monday,Tuesday\n10 AM,Erika,\n11 AM,,Yash\n") == \
        "Hours\nMonday | Tuesday\n10 AM · Monday: Erika\n11 AM · Tuesday: Yash"


async def test_fetch_follows_only_in_scope_pages_and_public_google_files():
    requests = []
    async with httpx.AsyncClient(transport=serve(requests)) as client:
        pages, warnings = await fetch_site(SITE, client)
    by_url = {url: (title, text) for url, title, text in pages}
    assert set(by_url) == {SITE, SITE + "schedule", "https://docs.google.com/spreadsheets/d/SHEET/edit"}
    assert by_url[SITE][0] == "DB Fall" and "## Exams\nExam 1 is Oct 19." in by_url[SITE][1]
    assert "Home | Blog" not in by_url[SITE][1]
    assert by_url["https://docs.google.com/spreadsheets/d/SHEET/edit"] == ("Office hours", "Hours\nMonday | Tuesday\n10 AM · Monday: Erika")
    assert not any("blog" in str(r.url) or "evil" in str(r.url) for r in requests)
    assert any("not shared publicly" in w for w in warnings) and any("notes.pdf: HTTP 404" in w for w in warnings)
    assert any("redirected outside the site" in w for w in warnings)


async def test_sync_sites_caches_logs_changes_and_never_sends_canvas_token(db):
    requests = []
    db.set_context(1, sites=[SITE])
    await sync_sites(db, transport=serve(requests))
    docs = {d["url"]: d for d in db.documents(1, "site")}
    assert SITE in docs and docs[SITE]["id"] == f"1:site:{SITE}"
    assert all("authorization" not in r.headers and "cookie" not in r.headers for r in requests)
    assert db.conn.execute("SELECT state FROM coverage WHERE kind='site'").fetchone()[0] == "partial"
    PAGES[SITE + "schedule"] = "<html><title>Schedule</title><p>Week 7 moved to week 8.</p></html>"
    try:
        await sync_sites(db, transport=serve(requests))
    finally:
        PAGES[SITE + "schedule"] = "<html><title>Schedule</title><p>Week 7: distributed systems.</p></html>"
    assert [(c["change"], c["title"]) for c in db.changes()] == [("changed", "Schedule")]
    db.set_context(1, sites=[])
    await sync_sites(db, transport=serve(requests))
    assert db.documents(1, "site") == []


async def test_answers_carry_notes_and_sites(db, config, monkeypatch):
    from canvas_rag import answer as module
    db.set_context(1, notes="We get a 48-hour extension on every homework.", sites=[SITE])
    seen = {}
    async def fake(c, prompt, on_text, lookup, on_tool):
        seen["prompt"] = prompt
        return "ok"
    monkeypatch.setattr(module, "generate", fake)
    await module.answer(config, db, "when is homework due?")
    assert "DS4300 Databases: We get a 48-hour extension on every homework." in seen["prompt"]
    assert f"DS4300 Databases: {SITE}" in seen["prompt"]
    from canvas_rag.tools import Lookup
    courses = await Lookup(db, config).call("list_courses", {})
    assert "Student's notes: We get a 48-hour extension" in courses and SITE in courses


def test_context_cli_sets_and_lists_notes(config, db):
    db.close()
    env = {**os.environ, "CANVAS_BUDDY_HOME": str(config.home)}
    out = subprocess.run([sys.executable, "-m", "canvas_rag", "context", "--course", "1", "--note", "Remote on Fridays"],
                         env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "1\tDS4300 Databases\n  notes: Remote on Fridays" in out.stdout
    out = subprocess.run([sys.executable, "-m", "canvas_rag", "context", "--note", "x"], env=env,
                         capture_output=True, text=True)
    assert out.returncode == 1 and "--course" in out.stderr


async def test_context_screen_saves_notes_adds_suggestions_and_fetches(config, db, monkeypatch):
    fetched = []
    async def fake_sync(database, progress=None, courses=None):
        fetched.append(courses)
        database.replace(1, "site", [record(1, "site", {"id": SITE, "title": "DB Fall", "html_url": SITE}, "",
                                            "Exam 1 is Oct 19.")])
    monkeypatch.setattr(ui, "sync_sites", fake_sync)
    app = ui.CanvasApp(config, db)
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.click("#context")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, Context)
        screen.query_one("#notes", TextArea).text = "Homework is on the course website."
        await pilot.pause()
        assert db.context()[1]["notes"] == "Homework is on the course website."
        suggestions = screen.query_one("#suggestions", OptionList)
        suggestions.highlighted = 0
        suggestions.action_select()
        await pilot.pause()
        assert db.context()[1]["sites"] == [SITE]
        assert "not fetched yet" in str(screen.query_one("#sites", OptionList).get_option_at_index(0).prompt)
        await pilot.click("#fetch-sites")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fetched == [None]
        assert "DS4300 Databases: 1 pages cached" in "\n".join(w._markdown for w in app.query("Markdown"))
        opened = []
        monkeypatch.setattr(app, "open_url", opened.append)
        app.open_library(f"1:site:{SITE}")
        await pilot.pause(0.3)
        library = app.screen
        assert isinstance(library, Library)
        assert str(library.query_one("#open-source", Button).label) == "Open website"
        assert str(library.query_one("#doc-tree").root.children[0].children[0].label) == "Course website (1)"
        await pilot.click("#open-source")
        assert opened == [SITE]
        await pilot.press("escape")
        app.open_planner()
        await pilot.pause()
        assert isinstance(app.screen, Planner)
        assert "Your note · DS4300: Homework is on the course website." in str(
            app.screen.query_one("#planner-summary", Static).render())


async def test_suggestions_inside_an_added_site_are_hidden(config, db):
    db.replace(1, "page", [record(1, "page", {"url": "p", "title": "Links", "body":
        f'<a href="{SITE}schedule">s</a>'}, BASE)])
    db.set_context(1, sites=[SITE])
    app = ui.CanvasApp(config, db)
    async with app.run_test(size=(120, 45)) as pilot:
        app.open_context()
        await pilot.pause()
        ids = [o.id for o in app.screen.query_one("#suggestions", OptionList).options]
        assert ids == ["https://other.example/x"]
