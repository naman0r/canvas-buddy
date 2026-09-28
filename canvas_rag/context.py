"""Course context: the student's own notes and course websites, per course. Everything saves as it changes."""
from urllib.parse import urlsplit

from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from .sites import within


class Context(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    CSS = """
    Context { align: center middle; }
    #context { width: 96%; height: 96%; padding: 0 1; border: round $accent; background: $surface; }
    #context Label { padding: 1 0 0 0; text-style: bold; }
    .hint { color: $text-muted; height: auto; }
    #notes { height: 8; }
    #sites, #suggestions { height: auto; max-height: 7; }
    #site-entry { height: 3; }
    #site-url { width: 1fr; }
    #context-state { height: auto; padding: 1 0 0 0; color: $text-muted; }
    .context-buttons { height: 3; dock: bottom; }
    """

    def __init__(self, db, course=None):
        super().__init__()
        self.db = db
        self.names = db.course_names()
        self.course = course if course in self.names else next(iter(self.names), None)

    def compose(self):
        with Vertical(id="context"):
            yield Select([(Text(name), cid) for cid, name in self.names.items()], value=self.course,
                         allow_blank=False, id="context-course")
            yield Label("Your notes")
            yield Static("Included in every question. Corrections and context Canvas lacks: \"we get a 48-hour "
                         "extension\", \"homework is on Pawtograder\", \"the Friday section is remote\".",
                         classes="hint", markup=False)
            yield TextArea(id="notes", soft_wrap=True)
            yield Label("Course websites")
            yield Static("Fetched at every sync with the pages and PDFs they link to on the same site; then "
                         "search, answers and the Library use them. Pages that need a login cannot be read.",
                         classes="hint", markup=False)
            yield OptionList(id="sites")
            with Horizontal(id="site-entry"):
                yield Input(placeholder="https://course-site.example/fall-2026/", id="site-url")
                yield Button("Add", id="add-site")
                yield Button("Remove selected", id="remove-site")
            yield Label("Linked from this course's Canvas pages")
            yield OptionList(id="suggestions")
            yield Static("", id="context-state", markup=False)
            with Horizontal(classes="context-buttons"):
                yield Button("Fetch websites now", id="fetch-sites", variant="primary")
                yield Button("Close", id="close")

    def on_mount(self):
        if self.course is None:
            self.query_one("#context-state", Static).update("Sync courses first; context belongs to a course.")
            return
        self.load_course()

    def load_course(self):
        entry = self.db.context().get(self.course, {"notes": "", "sites": []})
        self.query_one("#notes", TextArea).text = entry["notes"]
        sites = self.query_one("#sites", OptionList)
        sites.clear_options()
        pages = [d["url"] for d in self.db.documents(self.course, "site")]

        def cached(root):
            count = sum(within(p, root) for p in pages)
            return f"  · {count} pages cached" if count else "  · not fetched yet"
        sites.add_options([Option(url + cached(url), id=url) for url in entry["sites"]]
                          or [Option(Text("No course websites yet. Add one below or pick a link.", style="dim"),
                                     disabled=True)])
        suggestions = self.query_one("#suggestions", OptionList)
        suggestions.clear_options()
        # A link inside a site already added is fetched with it; suggesting it again would duplicate it.
        fresh = [u for u in self.db.suggested_sites(self.course) if not any(within(u, s) for s in entry["sites"])]
        suggestions.add_options([Option(url, id=url) for url in fresh]
                                or [Option(Text("No external links found in this course.", style="dim"),
                                           disabled=True)])

    @on(Select.Changed, "#context-course")
    def switch_course(self, event):
        self.course = event.value
        self.load_course()

    @on(TextArea.Changed, "#notes")
    def save_notes(self, event):
        # Loading a course's notes also fires Changed; only a real edit is saved and reported.
        text = event.text_area.text
        if self.course is not None and text.strip() != self.db.context().get(self.course, {}).get("notes", ""):
            self.db.set_context(self.course, notes=text)
            self.query_one("#context-state", Static).update("Notes saved.")

    def add_site(self, url):
        url = url.strip()
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username:
            self.query_one("#context-state", Static).update("Enter a full http(s) address, like https://site.edu/course/.")
            return
        sites = self.db.context().get(self.course, {"sites": []})["sites"]
        self.db.set_context(self.course, sites=sites + [parts._replace(fragment="").geturl()])
        self.query_one("#site-url", Input).value = ""
        self.load_course()
        self.query_one("#context-state", Static).update("Added. Fetch websites now, or it will be read at the next sync.")

    @on(Button.Pressed, "#add-site")
    @on(Input.Submitted, "#site-url")
    def add_typed(self):
        self.add_site(self.query_one("#site-url", Input).value)

    @on(OptionList.OptionSelected, "#suggestions")
    def add_suggestion(self, event):
        if event.option.id:
            self.add_site(event.option.id)

    @on(Button.Pressed, "#remove-site")
    def remove_site(self):
        option = self.query_one("#sites", OptionList).highlighted_option
        if option and option.id:
            sites = self.db.context()[self.course]["sites"]
            self.db.set_context(self.course, sites=[s for s in sites if s != option.id])
            self.load_course()
            self.query_one("#context-state", Static).update("Removed. Its cached pages go at the next fetch or sync.")

    @on(Button.Pressed, "#fetch-sites")
    def fetch_now(self):
        self.dismiss(True)

    @on(Button.Pressed, "#close")
    def action_close(self):
        self.dismiss(False)
