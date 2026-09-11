import asyncio
from time import monotonic

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Markdown, Select, SelectionList, Static

from .answer import answer
from .canvas import Canvas, sync

HELP = """Ask about policies, deadlines, announcements, or grades. Answers link to Canvas sources.

**/setup** choose courses · **/sync** refresh · **/browse** read everything cached

**/upcoming** next 30 days · **/overdue** past-due unsubmitted work · **/grades** Canvas grades

**/search words** local search · **/status** coverage · **/clear** new conversation

**/provider codex|opencode|ollama [model]** change model · **/help** this guide

Use the course filter to focus a question. Esc cancels work; Ctrl+Q quits.
Cache and embeddings stay on this computer. Codex/OpenCode send your question and selected
course excerpts to their model service. Nothing is posted or submitted to Canvas.
"""


class Setup(ModalScreen):
    CSS = """
    Setup { align: center middle; }
    #setup-box { width: 90%; height: 85%; padding: 1 2; border: round $accent; background: $surface; }
    SelectionList { height: 1fr; }
    #setup-state { height: auto; max-height: 3; }
    """

    def compose(self):
        with Vertical(id="setup-box"):
            yield Label("Connect Canvas · choose courses (Space toggles)")
            yield Input(self.app.config.url, placeholder="https://school.instructure.com", id="url")
            yield Button("Load courses", id="load", variant="primary")
            yield Static("CANVAS_PAT is read from your .env/environment.", id="setup-state", markup=False)
            yield SelectionList(id="courses")
            with Horizontal():
                yield Button("Save & sync", id="save", variant="success")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#load")
    @work(exclusive=True)
    async def load_courses(self):
        self.query_one("#setup-state", Static).update("Loading courses…")
        old = self.app.config.url
        self.app.config.url = self.query_one("#url", Input).value.strip().rstrip("/")
        try:
            async with Canvas(self.app.config) as api:
                courses = await api.courses()
            choices = self.query_one(SelectionList)
            choices.clear_options()
            for c in courses:
                if c.get("name"):
                    choices.add_option((f"{c['name']} ({c['id']})", c["id"], c["id"] in self.app.config.courses))
            self.loaded_url = self.app.config.url
            self.query_one("#setup-state", Static).update("Choose the classes to keep locally. Save replaces your selection.")
        except Exception as e:
            self.query_one("#setup-state", Static).update(str(e))
        finally:
            self.app.config.url = old

    @on(Button.Pressed, "#save")
    def save(self):
        selected = self.query_one(SelectionList).selected
        url = self.query_one("#url", Input).value.strip().rstrip("/")
        if not selected or getattr(self, "loaded_url", None) != url:
            self.query_one("#setup-state", Static).update("Load courses from this URL, then choose at least one.")
            return
        self.app.config.url = url
        self.app.config.courses = selected
        self.app.config.save()
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(False)


class Browser(ModalScreen):
    CSS = """
    Browser { align: center middle; }
    #browser { width: 95%; height: 95%; padding: 1; border: round $accent; background: $surface; }
    #document { height: 1fr; }
    #document-text { height: auto; }
    """

    def __init__(self, docs):
        super().__init__()
        self.docs = {d["id"]: d for d in docs}

    def compose(self):
        with Vertical(id="browser"):
            yield Label("Cached Canvas sources")
            yield Select([(f"{d['course']} · {d['kind']} · {d['title']}", d["id"])
                          for d in self.docs.values()], prompt="Choose a document", id="doc-picker")
            with VerticalScroll(id="document"):
                yield Static("Choose a document to read its full cached text.", id="document-text", markup=False)
            yield Button("Close", id="close")

    @on(Select.Changed, "#doc-picker")
    def selected(self, event):
        if event.value in self.docs:
            d = self.docs[event.value]
            self.query_one("#document-text", Static).update(
                f"{d['title']}\n{d['url']}\nSynced {d['synced']}\n\n{d['body']}")
            self.query_one("#document", VerticalScroll).scroll_home(animate=False)

    @on(Button.Pressed, "#close")
    def close(self):
        self.dismiss()


class CanvasApp(App):
    TITLE = "Canvas Buddy"
    SUB_TITLE = "Your classes, a question away"
    CSS = """
    Screen { background: $background; }
    #toolbar { height: 3; }
    #scope { width: 1fr; }
    #chat { height: 1fr; min-height: 0; padding: 0 2; }
    .message { margin: 1 0; padding: 0 1; border-left: thick $accent; height: auto; }
    .message.user { border-left: thick #f5c451; }
    .message.assistant { border-left: thick #5ccfe6; }
    .message.system { border-left: thick #7f8490; }
    #composer { dock: bottom; height: 6; padding: 0 1; }
    #status { height: 2; padding: 0 1; color: $text-muted; }
    #status.working { color: #5ccfe6; }
    #entry { height: 3; }
    #question { width: 1fr; height: 3; }
    #cancel-work { width: 10; min-width: 10; margin-left: 1; }

    """
    BINDINGS = [("ctrl+q", "quit", "Quit"), ("escape", "cancel", "Cancel"),
                ("ctrl+r", "refresh", "Sync"), ("ctrl+b", "browse", "Browse")]

    def __init__(self, config, db):
        super().__init__()
        self.config, self.db = config, db
        self.history = []
        self.busy = False
        self.status_text = ""
        self.started_at = 0.0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="toolbar"):
            yield Select([("All selected courses", 0)], value=0, allow_blank=False, id="scope")
            yield Button("Courses", id="setup")
            yield Button("Sync", id="sync")
            yield Button("Browse", id="browse")
        yield VerticalScroll(id="chat")
        with Vertical(id="composer"):
            yield Static("", id="status", markup=False)
            with Horizontal(id="entry"):
                yield Input(placeholder="Ask a question or reply here…  /help", id="question")
                yield Button("Cancel", id="cancel-work", disabled=True)

        yield Footer()

    async def on_mount(self):
        self.status_widget = self.query_one("#status", Static)
        self.set_interval(0.2, self.render_status)
        self.refresh_courses()
        await self.say(HELP)
        self.set_status(f"Ready · {self.config.provider} · {len(self.db.documents())} cached documents")
        self.query_one("#question", Input).focus()
        if not self.config.courses:
            self.show_setup()

    def refresh_courses(self):
        self.query_one("#scope", Select).set_options([("All selected courses", 0)] +
            [(d["title"], d["course"]) for d in self.db.documents(kind="course")])
        self.query_one("#scope", Select).value = 0

    @property
    def course(self):
        return self.query_one("#scope", Select).value or None

    async def say(self, text, role="system"):
        chat = self.query_one("#chat", VerticalScroll)
        await chat.mount(Markdown(text, classes=f"message {role}"))
        chat.scroll_end(animate=False)

    def set_status(self, text):
        self.status_text = text
        self.render_status()

    def render_status(self):
        status = self.status_widget
        status.set_class(self.busy, "working")
        text = self.status_text
        if self.busy:
            elapsed = monotonic() - self.started_at
            spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(elapsed * 5) % 10]
            hint = " · Still waiting; Esc cancels" if elapsed >= 30 else " · Esc cancels"
            text = f"{spinner} {text} · {int(elapsed)}s{hint}"
        status.update(text)

    def begin_work(self, message):
        self.busy = True
        self.started_at = monotonic()
        self.query_one("#cancel-work", Button).disabled = False
        for selector in ("#scope", "#setup", "#sync", "#browse"):
            self.query_one(selector).disabled = True
        self.set_status(message)

    def finish_work(self, message):
        self.busy = False
        self.query_one("#cancel-work", Button).disabled = True
        for selector in ("#scope", "#setup", "#sync", "#browse"):
            self.query_one(selector).disabled = False
        self.set_status(message)
        self.query_one("#question", Input).focus()


    def show_setup(self):
        if not self.busy:
            self.push_screen(Setup(), lambda saved: self.run_sync() if saved else None)

    @on(Button.Pressed)
    def button(self, event):
        if event.button.id == "setup":
            self.show_setup()
        elif event.button.id == "sync":
            self.action_refresh()
        elif event.button.id == "browse":
            self.action_browse()
        elif event.button.id == "cancel-work":
            self.action_cancel()

    def action_browse(self):
        if not self.busy:
            self.push_screen(Browser(self.db.documents(self.course)))

    def action_refresh(self):
        if not self.busy:
            self.run_sync()

    def action_cancel(self):
        if self.busy:
            self.set_status("Cancelling…")
            self.workers.cancel_all()

    @work(group="operation", exclusive=True)
    async def run_sync(self):
        self.begin_work("Connecting to Canvas…")
        outcome = "Ready · Sync complete"
        try:
            if not self.config.courses:
                outcome = "Choose courses with /setup first."
                await self.say(outcome)
                return
            await sync(self.config, self.db, self.set_status)
            self.refresh_courses()
        except asyncio.CancelledError:
            outcome = "Cancelled · Completed sync sections remain cached"
            raise
        except Exception as e:
            outcome = "Sync failed · See message above; you can try again"
            await self.say(f"Sync failed: {e}")
        finally:
            self.finish_work(outcome)

    @on(Input.Submitted, "#question")
    async def submitted(self, event):
        text = event.value.strip()
        if not text:
            return
        if self.busy:
            return
        event.input.value = ""
        if text.startswith("/"):
            await self.command(text)
        else:
            self.ask(text)

    async def command(self, text):
        cmd, _, arg = text.partition(" ")
        if cmd == "/setup":
            self.show_setup()
        elif cmd == "/sync":
            self.run_sync()
        elif cmd == "/browse":
            self.action_browse()
        elif cmd in {"/upcoming", "/overdue"}:
            rows = self.db.upcoming(self.course, overdue=cmd == "/overdue")
            await self.say("**Cached deadlines** (local timezone; refresh with /sync)\n\n" + ("\n".join(
                f"- {r['due']} · [{r['title']}]({r['url']}) · {r['state']}" for r in rows)
                or "No matching dated work in the cache. Check /status for sync coverage."))
        elif cmd == "/grades":
            await self.say("**Canvas grade snapshot**\n\n" + self.db.grades(self.course))
        elif cmd == "/status":
            await self.say("```text\n" + self.db.status() + "\n```")
        elif cmd == "/clear":
            self.history.clear()
            await self.query_one("#chat", VerticalScroll).remove_children()
            await self.say("New conversation.")
        elif cmd == "/search" and arg:
            self.search(arg)
        elif cmd == "/provider":
            parts = arg.split(maxsplit=1)
            if not parts or parts[0] not in {"codex", "opencode", "ollama"}:
                await self.say("Usage: /provider codex|opencode|ollama [model]")
                return
            self.config.provider = parts[0]
            self.config.model = parts[1] if len(parts) > 1 else ""
            self.config.save()
            self.set_status(f"Ready · {self.config.provider} · model: {self.config.model or 'CLI default'}")
        else:
            await self.say(HELP)

    @work(group="operation", exclusive=True)
    async def search(self, text):
        self.begin_work("Searching your courses…")
        outcome = "Ready · Search complete"
        try:
            rows = await self.db.search(text, self.config, self.course)
            await self.say("\n\n".join(f"[{r['title']}]({r['url']})\n\n{r['text']}" for r in rows)
                           or "No matching cached sources.")
        except asyncio.CancelledError:
            outcome = "Cancelled · Ready for your next question"
            raise
        except Exception as e:
            outcome = "Search failed · You can try again"
            await self.say(f"Could not search: {e}")
        finally:
            self.finish_work(outcome)

    @work(group="operation", exclusive=True)
    async def ask(self, question):
        self.begin_work("Searching your courses…")
        outcome = "Ready · Reply below or choose a class above"
        try:
            await self.say("**You:** " + question, role="user")
            result, sources = await answer(self.config, self.db, question, self.course, self.history,
                                           progress=self.set_status)
            await self.say("**Canvas Buddy:**\n\n" + result, role="assistant")
            self.history.append((question, result))
            self.history = self.history[-3:]
        except asyncio.CancelledError:
            outcome = "Cancelled · Ready for your next question"
            raise
        except TimeoutError:
            outcome = "Timed out · Try again or change /provider"
            await self.say("The model did not reply within 4 minutes. Try again or change /provider.")
        except Exception as e:
            outcome = "Could not answer · Try again or change /provider"
            await self.say(f"Could not answer: {e or type(e).__name__}")
        finally:
            self.finish_work(outcome)
