import pytest
from textual.widgets import Button, MarkdownViewer, Static, Tree

from canvas_rag.canvas import record
from canvas_rag.config import Config
from canvas_rag.library import Library, anchor, module_items, reading
from canvas_rag.store import Store
from canvas_rag import ui

BASE = "https://canvas.example"


@pytest.fixture
def config(tmp_path):
    return Config(home=tmp_path, url=BASE, token="t", courses=[1], embed_model="")


@pytest.fixture
def db(config):
    db = Store(config.home)
    db.replace(1, "course", [record(1, "course", {"id": 1, "name": "Biology 101",
                                                  "syllabus_body": "<p>Attend every lab.</p>"}, BASE)])
    db.replace(1, "page", [record(1, "page", {"url": "week-1", "title": "Week 1 overview"}, BASE, "Read chapter 1.")])
    db.replace(1, "file", [record(1, "file", {"id": 9, "display_name": "Lecture 1.pptx"}, BASE,
                                  "Slide 1\nCells\nSlide 2\nMitochondria *make* ATP\nSlide 3\nReview")])
    db.replace(1, "module", [record(1, "module", {"id": 4, "name": "Week 1", "position": 1, "items": [
        {"type": "SubHeader", "title": "Before class"},
        {"type": "Page", "title": "Week 1 overview", "page_url": "week-1"},
        {"type": "File", "title": "Lecture 1.pptx", "content_id": 9, "indent": 1},
        {"type": "ExternalUrl", "title": "Textbook site"}]}, BASE)])
    yield db
    db.close()


def test_module_items_link_cached_documents(db):
    module = db.get("1:module:4")
    assert module_items(module, db.documents(1)) == [
        ("Before class", None, 0, "SubHeader"), ("Week 1 overview", "1:page:week-1", 0, "Page"),
        ("Lecture 1.pptx", "1:file:9", 1, "File"), ("Textbook site", None, 0, "ExternalUrl")]


def test_reading_turns_markers_into_headings_and_escapes_text(db):
    text = reading(db.get("1:file:9"), "Biology 101")
    assert text.startswith("# Lecture 1.pptx\n\n_Biology 101 · file · synced ")
    assert "### Slide 2\n\nMitochondria \\*make\\* ATP" in text
    assert anchor("Slide 2–3") == "slide-2" and anchor("page-4") == "page-4" and anchor(None) is None


async def test_library_arranges_courses_and_opens_at_a_slide(config, db):
    app = ui.CanvasApp(config, db)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_library("1:file:9", "slide-3")
        await pilot.pause(0.5)
        screen = app.screen
        assert isinstance(screen, Library)
        tree = screen.query_one("#doc-tree", Tree)
        course = tree.root.children[0]
        assert [str(n.label) for n in course.children][:3] == ["Syllabus", "Modules (1)", "Pages (1)"]
        week = course.children[1].children[0]
        assert [n.data for n in week.children] == [None, "1:page:week-1", "1:file:9", None]
        assert tree.cursor_node.data == "1:file:9"
        viewer = screen.query_one("#doc-view", MarkdownViewer)
        assert viewer.show_table_of_contents
        assert "### Slide 3" in viewer.document.source
        assert not screen.query_one("#ask-doc", Button).disabled


async def test_ask_about_this_pins_the_document(config, db, monkeypatch):
    seen = {}
    async def fake_answer(*args, pinned=None, **kwargs):
        seen["pinned"] = pinned
        return "Mitochondria make ATP.", [{"id": "1:file:9", "title": "Lecture 1.pptx", "url": f"{BASE}/courses/1/files/9",
                                           "location": "Slide 2", "excerpt": "x"}]
    monkeypatch.setattr(ui, "answer", fake_answer)
    app = ui.CanvasApp(config, db)
    async with app.run_test(size=(120, 40)) as pilot:
        app.open_library("1:file:9")
        await pilot.pause(0.3)
        await pilot.click("#ask-doc")
        await pilot.pause()
        pin = app.query_one("#pinned", Static)
        assert pin.display and "About: Lecture 1.pptx" in str(pin.render())
        app.ask("what makes ATP?")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert seen["pinned"] == "1:file:9"
        await app.command("/clear")
        assert not app.query_one("#pinned", Static).display and app.pinned is None


def test_sources_line_lists_cited_and_read_documents():
    sources = [{"id": "1:file:9", "title": "Lecture 1.pptx", "url": "https://c/1/files/9", "location": "Slide 2–3",
                "excerpt": "x"},
               {"id": "1:page:x", "title": "Uncited", "url": "https://c/p", "location": None, "excerpt": "x"},
               {"id": "1:page:week-1", "title": "Week 1", "url": "https://c/w", "location": None, "excerpt": ""}]
    line = ui.cited("See [slides](https://c/1/files/9).", sources)
    assert line == ("\n\n**Sources** · [Lecture 1.pptx · Slide 2–3](source:1%3Afile%3A9#slide-2) · "
                    "[Week 1](source:1%3Apage%3Aweek-1)")
    assert ui.cited("No links.", sources[:2]) == ""
