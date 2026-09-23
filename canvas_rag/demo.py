"""Disposable fictional data. Demo never loads saved settings or credentials."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from .canvas import record
from .config import Config
from .store import Store


def seed(home):
    config = Config(home=home, url="https://canvas.example", courses=[101], embed_model="")
    db = Store(home)
    due = (datetime.now(timezone.utc) + timedelta(days=3)).replace(hour=21, minute=0).isoformat()
    samples = [
        ("course", {"id": 101, "name": "DEMO · Introduction to Astronomy"}, "Fictional course for exploring Canvas Buddy."),
        ("page", {"id": 1, "title": "Syllabus", "html_url": "https://canvas.example/courses/101/pages/syllabus"},
         "Attendance: attend lectures or watch the recording before Friday. Office hours: Tuesday 2–4 PM. "
         "The midterm exam covers chapters 1–4; its date has not been announced."),
        ("assignment", {"id": 2, "name": "Moon observation journal", "due_at": due, "points_possible": 20,
                        "submission": {"workflow_state": "unsubmitted"},
                        "html_url": "https://canvas.example/courses/101/assignments/2"},
         "Observe the moon on three nights and describe how its shape changes."),
        ("grade", {"id": 3, "grades": {"current_score": 92, "current_grade": "A-", "final_score": None}}, None),
        ("announcement", {"id": 4, "title": "Observatory visit",
                          "html_url": "https://canvas.example/courses/101/discussion_topics/4"},
         "Bring a warm layer to the observatory visit. We will reschedule if the weather is cloudy."),
    ]
    for kind, item, body in samples:
        db.replace(101, kind, [record(101, kind, item, config.url, body)])
        db.coverage(101, kind, "ok", "Fictional demo data")
    db.change(101, "announcement", "new", "Observatory visit", "posted since last sync")
    db.change(101, "assignment", "changed", "Moon observation journal",
              "deadline moved from Sep 02, 21:00 to " + datetime.fromisoformat(due.replace("Z", "+00:00"))
              .astimezone().strftime("%b %d, %H:%M"))
    db.change(101, "grade", "changed", "Introduction to Astronomy", "current score 89 → 92")
    return config, db


def run():
    from .ui import CanvasApp
    with TemporaryDirectory(prefix="canvas-buddy-demo-") as directory:
        config, db = seed(Path(directory))
        try:
            CanvasApp(config, db, demo=True).run()
        finally:
            db.close()
