from datetime import datetime, timedelta, timezone

import pytest
from textual.widgets import OptionList

from canvas_rag import ui
from canvas_rag.canvas import record
from canvas_rag.config import Config
from canvas_rag.planner import Planner, agenda, code
from canvas_rag.store import Store, short, status

BASE = "https://canvas.example"


def test_short_names_and_codes():
    assert short("ACCT2301 10357 Profit Analysis Manager Advis SEC 05 Fall 2026 [BOS-1-TR]") == \
        "ACCT2301 Profit Analysis Manager Advis"
    assert short("Intro to Astronomy") == "Intro to Astronomy"
    assert code("FINA3303 Investments") == "FINA3303" and code("Intro to Astronomy") == "Intro to A"


def test_status_words():
    assert status({}) == "to do"
    assert status({"submission": {"missing": True}}) == "missing"
    assert status({"submission": {"workflow_state": "graded", "late": True}}) == "late"
    assert status({"submission": {"workflow_state": "submitted"}}) == "done"
    assert status({"submission": {"excused": True}}) == "excused"


@pytest.fixture
def db(tmp_path):
    db = Store(tmp_path)
    now = datetime.now(timezone.utc)

    def due(days):
        return (now + timedelta(days=days)).isoformat()
    db.replace(1, "course", [record(1, "course", {"id": 1, "name": "BIOL1101 12345 Biology SEC 01 Fall 2026"}, BASE)])
    db.replace(1, "assignment", [
        record(1, "assignment", {"id": 1, "name": "Lab 1", "due_at": due(-2), "points_possible": 10}, BASE),
        record(1, "assignment", {"id": 2, "name": "Lab 2", "due_at": due(3), "points_possible": 10}, BASE),
        record(1, "assignment", {"id": 3, "name": "Essay", "due_at": due(3), "points_possible": 20,
                                 "submission": {"workflow_state": "submitted"}}, BASE),
        record(1, "assignment", {"id": 4, "name": "Project"}, BASE),
        record(1, "assignment", {"id": 5, "name": "Done undated", "submission": {"workflow_state": "graded"}}, BASE),
        record(1, "assignment", {"id": 6, "name": "Far off", "due_at": due(20)}, BASE)])
    yield db
    db.close()


def test_agenda_orders_overdue_days_then_undated(db):
    rows, count, days = agenda(db, None, 14)
    ids = [doc for _, doc in rows]
    assert ids == [None, "1:assignment:1", None, "1:assignment:3", "1:assignment:2", None, "1:assignment:4"]
    assert (count, days) == (2, 1)
    labels = [str(label) for label, _ in rows]
    assert labels[0] == "Overdue · 1" and "2 due · 30 pts" in labels[2]
    assert "BIOL1101" in labels[1] and "done" in labels[3] and labels[5] == "No due date in Canvas · 1"
    assert "1:assignment:6" in [doc for _, doc in agenda(db, None, 30)[0]]


async def test_planner_opens_items_and_pins(db, tmp_path):
    config = Config(home=tmp_path, url=BASE, token="t", courses=[1])
    app = ui.CanvasApp(config, db)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#upcoming")
        await pilot.pause()
        assert isinstance(app.screen, Planner)
        agenda_list = app.screen.query_one("#agenda", OptionList)
        assert agenda_list.highlighted_option.id == "1:assignment:1"
        await pilot.press("a")
        await pilot.pause()
        assert app.pinned == "1:assignment:1"
