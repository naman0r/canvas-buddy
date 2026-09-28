"""The Planner: dated work grouped by day, overdue work first, undated work last."""
import re
from datetime import datetime, timedelta

from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static
from textual.widgets.option_list import Option

# Course colours, in the order courses are listed; the first two match the chat bars.
COLOURS = ["#5ccfe6", "#f5c451", "#c3a6ff", "#95e6cb", "#ffa759", "#f28779", "#bae67e", "#73d0ff"]
STATE = {"to do": "", "missing": "bold #f28779", "late": "#ffa759", "done": "#95e6cb", "excused": "dim",
         "event": "#c3a6ff"}


def code(name):
    """The catalogue code when a course name starts with one ('FINA3303 Investments' → 'FINA3303')."""
    first = name.split(" ", 1)[0]
    return first if re.fullmatch(r"[A-Z]{2,5}\d{3,5}[A-Z]?", first) else name[:10]


def day_label(day, today):
    if day == today:
        return "Today · " + day.strftime("%a %b %d")
    if day == today + timedelta(days=1):
        return "Tomorrow · " + day.strftime("%a %b %d")
    return day.strftime("%A %b %d")


def agenda(db, course, days):
    """Planner rows as (label, doc id or None). A None id is a heading."""
    names = db.course_names()
    colour = {c: COLOURS[i % len(COLOURS)] for i, c in enumerate(sorted(names))}
    today = datetime.now().astimezone().date()

    def row(d, when):
        text = Text("  ")
        text.append(f"{when:>9}  ", style="dim")
        text.append(f"{code(names.get(d['course'], '')):<10}", style=colour.get(d["course"], ""))
        text.append(d["title"])
        points = d["raw"].get("points_possible")
        if points:
            text.append(f"  {points:g} pts", style="dim")
        text.append(f"  {d['state']}", style=STATE.get(d["state"], ""))
        return text, d["id"]

    rows = []
    overdue = db.upcoming(course, overdue=True)
    if overdue:
        rows.append((Text(f"Overdue · {len(overdue)}", style="bold #f28779"), None))
        rows += [row(d, datetime.fromisoformat(d["due"]).strftime("%b %d")) for d in overdue]
    by_day, dated = {}, db.upcoming(course, days=days)
    for d in dated:
        by_day.setdefault(datetime.fromisoformat(d["due"]).date(), []).append(d)
    for day, items in by_day.items():
        points = sum(d["raw"].get("points_possible") or 0 for d in items)
        heading = Text(day_label(day, today), style="bold")
        heading.append(f"  {len(items)} due" + (f" · {points:g} pts" if points else ""), style="dim")
        rows.append((heading, None))
        rows += [row(d, datetime.fromisoformat(d["due"]).strftime("%I:%M %p").lstrip("0")) for d in items]
    undated = db.undated(course)
    if undated:
        rows.append((Text(f"No due date in Canvas · {len(undated)}", style="bold"), None))
        rows += [row({**d, "state": "to do"}, "—") for d in undated]
    return rows, len(dated), len(by_day)


class Planner(ModalScreen):
    BINDINGS = [("escape", "close", "Close"), ("a", "ask", "Ask about this"),
                ("1", "window(7)", "Week"), ("2", "window(14)", "2 weeks"), ("3", "window(30)", "Month")]
    CSS = """
    Planner { align: center middle; }
    #planner { width: 98%; height: 96%; padding: 0 1; border: round $accent; background: $surface; }
    #planner-top { height: 3; }
    #planner-title { width: auto; padding: 1 2 0 0; text-style: bold; }
    #planner-top Button { min-width: 10; }
    #planner-summary { height: auto; padding: 0 1 1 1; color: $text-muted; }
    #agenda { height: 1fr; }
    /* Day headings are disabled so the cursor skips them, but must not look greyed out. */
    #agenda > .option-list--option-disabled { color: $text; text-style: none; }
    .planner-buttons { height: 3; }
    """

    def __init__(self, db, course=None, days=14):
        super().__init__()
        self.db, self.course, self.days = db, course, days

    def compose(self):
        with Vertical(id="planner"):
            with Horizontal(id="planner-top"):
                yield Label("Planner", id="planner-title")
                yield Button("Week", id="w7")
                yield Button("2 weeks", id="w14")
                yield Button("Month", id="w30")
            yield Static("", id="planner-summary", markup=False)
            yield OptionList(id="agenda")
            with Horizontal(classes="planner-buttons"):
                yield Button("Open", id="open-item", variant="primary")
                yield Button("Ask about this", id="ask-item")
                yield Button("Close", id="close")

    def on_mount(self):
        self.fill()
        self.query_one("#agenda", OptionList).focus()

    def fill(self):
        rows, count, days = agenda(self.db, self.course, self.days)
        agenda_list = self.query_one("#agenda", OptionList)
        agenda_list.clear_options()
        agenda_list.add_options([Option(label, id=doc, disabled=doc is None) for label, doc in rows]
                                or [Option(Text("Nothing dated or undone in the cache. /status shows sync coverage.",
                                                style="dim"), disabled=True)])
        first = next((i for i, (_, doc) in enumerate(rows) if doc), None)
        if first is not None:
            agenda_list.highlighted = first
        names = self.db.course_names()
        # The student's notes often correct these dates ("48-hour extension"), so they sit above them.
        notes = [f"{code(names[c])}: {v['notes']}" for c, v in self.db.context().items()
                 if v["notes"] and c in names and (not self.course or c == self.course)]
        self.query_one("#planner-summary", Static).update(
            f"Next {self.days} days: {count} items on {days} days · times are local · "
            "Enter opens · a asks about it · 1/2/3 change the window"
            + "".join(f"\nYour note · {n}" for n in notes))
        for n in (7, 14, 30):
            self.query_one(f"#w{n}", Button).variant = "primary" if n == self.days else "default"

    def action_window(self, days):
        self.days = days
        self.fill()

    @on(Button.Pressed, "#w7, #w14, #w30")
    def window_button(self, event):
        self.action_window(int(event.button.id[1:]))

    def selected_doc(self):
        agenda_list = self.query_one("#agenda", OptionList)
        option = agenda_list.highlighted_option
        return option.id if option and not option.disabled else None

    @on(OptionList.OptionSelected, "#agenda")
    @on(Button.Pressed, "#open-item")
    def open_item(self):
        if doc := self.selected_doc():
            self.app.open_library(doc)

    @on(Button.Pressed, "#ask-item")
    def action_ask(self):
        if doc := self.selected_doc():
            self.dismiss(doc)

    @on(Button.Pressed, "#close")
    def action_close(self):
        self.dismiss(None)
