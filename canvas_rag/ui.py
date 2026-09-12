import asyncio
from dataclasses import replace
import shutil
from time import monotonic
from urllib.parse import unquote, urlsplit

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Markdown, Select, SelectionList, Static

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
    #setup-box { width: 95%; height: 95%; padding: 1 2; border: round $accent; background: $surface; }
    #connection { height: 1fr; }
    #courses { height: 1fr; display: none; }
    #setup-state { height: auto; max-height: 4; }
    #setup-buttons { height: 3; }
    """
    BINDINGS = [("escape", "cancel_setup", "Cancel")]

    def compose(self):
        c = self.app.config
        with Vertical(id="setup-box"):
            yield Label("Canvas Buddy · Personal testing setup")
            with VerticalScroll(id="connection"):
                yield Label("Canvas site URL")
                yield Input(c.url, placeholder="https://your-school.instructure.com", id="url")
                yield Label("Personal access token")
                yield Input(password=True, placeholder="Canvas access token (blank keeps saved token)" if c.token
                            else "Paste your Canvas access token", id="token")
                yield Static("Canvas → Account → Settings → New Access Token.\n"
                             "Choose an expiry date. If New Access Token is missing, your school may disable tokens.\n"
                             "Saved on this computer in an owner-only file; never sent to your chat model.", markup=False)
                yield Label("Answer provider (log in with its CLI first)")
                yield Select([(f"{name}" + (" · not installed" if not shutil.which(name) else ""), name)
                              for name in ("codex", "opencode", "ollama")], value=c.provider,
                             allow_blank=False, id="provider")
                yield Static("", id="provider-help", markup=False)
                yield Input(c.model, placeholder="Model (blank uses provider default; Ollama needs an installed model)", id="model")
                yield Checkbox("Use local Ollama embeddings (optional)", value=bool(c.embed_model), id="embeddings")
                yield Static("Keyword search works without Ollama. For embeddings: ollama pull nomic-embed-text\n"
                             "Codex/OpenCode send selected course text to their model service.\n"
                             "Need a CLI? Run canvas-buddy doctor for installation/login instructions.", markup=False)
            yield Static("Enter your Canvas URL and token, then load courses.", id="setup-state", markup=False)
            yield SelectionList(id="courses")
            with Horizontal(id="setup-buttons"):
                yield Button("Load courses", id="load", variant="primary")
                yield Button("Back", id="back", disabled=True)
                yield Button("Save & sync", id="save", variant="success", disabled=True)
                yield Button("Cancel", id="cancel")

    @on(Select.Changed, "#provider")
    def provider_help(self):
        from .diagnostics import GUIDANCE
        name = self.query_one("#provider", Select).value
        self.query_one("#provider-help", Static).update(
            GUIDANCE[name] + "\nYou can sync, browse, and check deadlines before model login.")

    @on(Button.Pressed, "#load")
    @work(exclusive=True)
    async def load_courses(self):
        self.query_one("#load", Button).disabled = True
        self.query_one("#setup-state", Static).update("Connecting to Canvas…")
        c = self.app.config
        url = self.query_one("#url", Input).value.strip().rstrip("/").removesuffix("/api/v1")
        typed_token = self.query_one("#token", Input).value.strip()
        self.draft = replace(c, url=url, token=typed_token or (c.token if url == c.url else ""),
                             provider=self.query_one("#provider", Select).value,
                             model=self.query_one("#model", Input).value.strip(),
                             embed_model=(c.embed_model or "nomic-embed-text")
                             if self.query_one("#embeddings", Checkbox).value else "")
        try:
            if self.draft.provider == "ollama" and not self.draft.model:
                raise ValueError("Enter an installed Ollama chat model name (run ollama list).")
            async with Canvas(self.draft) as api:
                profile = await api.one("users/self/profile")
                self.user_id = str(profile["id"])
                self.app.db.check_identity(url, self.user_id)
                courses = await api.courses()
            choices = self.query_one(SelectionList)
            choices.clear_options()
            for course in courses:
                if course.get("name"):
                    choices.add_option((Text(f"{course['name']} ({course['id']})"), course["id"],
                                        course["id"] in c.courses and url == c.url))
            if not choices.option_count:
                self.query_one("#setup-state", Static).update("No student courses found. Check that this is your student Canvas account.")
                return
            self.query_one("#connection").display = False
            choices.display = True
            choices.focus()
            self.query_one("#load").display = False
            self.query_one("#back", Button).disabled = False
            self.query_one("#save", Button).disabled = False
            self.query_one("#setup-state", Static).update("Select courses with Space, then Save & sync. Unselected courses are removed locally.")
        except Exception as e:
            self.query_one("#setup-state", Static).update(self.draft.error(e))
        finally:
            self.query_one("#load", Button).disabled = False

    @on(Button.Pressed, "#back")
    def back(self):
        self.query_one("#connection").display = True
        self.query_one("#courses").display = False
        self.query_one("#load").display = True
        self.query_one("#save", Button).disabled = True
        self.query_one("#back", Button).disabled = True

    @on(Button.Pressed, "#save")
    def save(self):
        selected = self.query_one(SelectionList).selected
        if not selected:
            self.query_one("#setup-state", Static).update("Select at least one course.")
            return
        try:
            self.draft.courses = list(selected)
            self.app.db.bind(self.draft.url, self.user_id)
            self.draft.save_token()
            self.draft.save()
            self.app.db.prune_courses(selected)
        except (OSError, ValueError) as e:
            self.query_one("#setup-state", Static).update(str(e))
            return
        self.app.config = self.draft
        self.app.history.clear()
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel_setup(self):
        self.workers.cancel_all()
        self.dismiss(False)


class Browser(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
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
            yield Input(placeholder="Filter by title, type, or course…", id="doc-filter")
            yield Select([(Text(f"{d['course']} · {d['kind']} · {d['title']}"), d["id"])
                          for d in self.docs.values()], prompt="Choose a document", id="doc-picker")
            with VerticalScroll(id="document"):
                yield Static("Choose a document to read its full cached text.", id="document-text", markup=False)
            with Horizontal(classes="browser-buttons"):
                yield Button("Open in Canvas", id="open-source", disabled=True)
                yield Button("Close", id="close")

    @on(Input.Changed, "#doc-filter")
    def filter_documents(self, event):
        words = event.value.casefold().split()
        self.query_one("#doc-picker", Select).set_options([
            (Text(f"{d['course']} · {d['kind']} · {d['title']}"), d["id"]) for d in self.docs.values()
            if all(w in f"{d['course']} {d['kind']} {d['title']}".casefold() for w in words)])
        self.query_one("#document-text", Static).update("Choose a matching document above.")
        self.query_one("#open-source", Button).disabled = True

    @on(Select.Changed, "#doc-picker")
    def selected(self, event):
        if event.value in self.docs:
            d = self.docs[event.value]
            self.query_one("#document-text", Static).update(
                f"{d['title']}\n{d['url']}\nSynced {d['synced']}\n\n{d['body']}")
            self.query_one("#document", VerticalScroll).scroll_home(animate=False)
            self.query_one("#open-source", Button).disabled = False

    @on(Button.Pressed, "#open-source")
    def open_source(self):
        doc = self.docs.get(self.query_one("#doc-picker", Select).value)
        if doc:
            self.app.open_source(doc["url"])

    @on(Button.Pressed, "#close")
    def action_close(self):
        self.dismiss()


class CanvasApp(App):
    TITLE = "Canvas Buddy"
    SUB_TITLE = "Your classes, a question away"
    CSS = """
    Screen { background: $background; }
    #toolbar { height: 3; }
    #shortcuts, .browser-buttons { height: 3; }
    #snapshot { height: auto; max-height: 3; padding: 0 2; color: $text-muted; }
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

    def __init__(self, config, db, setup=False, demo=False):
        super().__init__()
        self.config, self.db = config, db
        self.start_setup = setup
        self.demo = demo
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
        with Horizontal(id="shortcuts"):
            yield Button("Upcoming", id="upcoming")
            yield Button("Grades", id="grades")
            yield Button("Coverage", id="coverage")
        yield Static("", id="snapshot", markup=False)
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
        if self.demo:
            await self.say("**Demo · Fictional classes, no network or model calls.**\n\n"
                           "Try Upcoming, Grades, Browse, or ask about attendance. Replies show matching sample excerpts. "
                           "The Canvas connection currently supports personal testing; broader use needs OAuth.")
        self.set_status(f"Ready · {self.config.provider} · {len(self.db.documents())} cached documents")
        self.query_one("#question", Input).focus()
        if self.start_setup or not self.config.courses:
            self.show_setup()

    def refresh_courses(self):
        self.query_one("#scope", Select).set_options([("All selected courses", 0)] +
            [(Text(d["title"]), d["course"]) for d in self.db.documents(kind="course")])
        self.query_one("#scope", Select).value = 0
        self.query_one("#snapshot", Static).update(("DEMO · " if self.demo else "") + self.db.snapshot_summary())

    @property
    def course(self):
        return self.query_one("#scope", Select).value or None

    async def say(self, text, role="system"):
        chat = self.query_one("#chat", VerticalScroll)
        await chat.mount(Markdown(text, classes=f"message {role}", open_links=False))
        chat.scroll_end(animate=False)

    @on(Markdown.LinkClicked)
    def link_clicked(self, event):
        event.stop()
        self.open_source(event.href)

    def open_source(self, href):
        # Never hand model-generated URLs to the OS. Only known Canvas sources can open.
        for row in self.db.conn.execute("SELECT DISTINCT url FROM documents"):
            url = row[0]
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            if (unquote(url) == unquote(href) and parsed.scheme == "https" and not parsed.username
                    and parsed.netloc == urlsplit(self.config.url).netloc
                    and not parsed.query and not parsed.fragment and not self.demo):
                self.open_url(url)
                return
        self.notify("Only cached Canvas source links can open. Use Browse to inspect sources.", severity="warning")

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
        for selector in ("#scope", "#setup", "#sync", "#browse", "#upcoming", "#grades", "#coverage"):
            self.query_one(selector).disabled = True
        self.set_status(message)

    def finish_work(self, message):
        self.busy = False
        self.query_one("#cancel-work", Button).disabled = True
        for selector in ("#scope", "#setup", "#sync", "#browse", "#upcoming", "#grades", "#coverage"):
            self.query_one(selector).disabled = False
        self.set_status(message)
        self.query_one("#question", Input).focus()


    def show_setup(self):
        if self.demo:
            self.notify("Demo uses fictional courses. Canvas setup is currently for personal testing.")
            return
        if not self.busy:
            self.push_screen(Setup(), lambda saved: self.run_sync() if saved else self.query_one("#question", Input).focus())

    @on(Button.Pressed)
    async def button(self, event):
        if event.button.id == "setup":
            self.show_setup()
        elif event.button.id == "sync":
            self.action_refresh()
        elif event.button.id == "browse":
            self.action_browse()
        elif event.button.id == "cancel-work":
            self.action_cancel()
        elif event.button.id in {"upcoming", "grades", "coverage"}:
            await self.command("/status" if event.button.id == "coverage" else "/" + event.button.id)

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
        if self.demo:
            self.notify("Demo data is fictional and never syncs with Canvas.")
            return
        self.begin_work("Connecting to Canvas…")
        outcome = "Ready · Sync complete"
        try:
            if not self.config.courses:
                outcome = "Choose courses with /setup first."
                await self.say(outcome)
                return
            await sync(self.config, self.db, self.set_status)
            self.refresh_courses()
            await self.say(self.db.snapshot_summary())
            outcome = "Ready · Sync finished; see coverage above"
        except asyncio.CancelledError:
            outcome = "Cancelled · Completed sync sections remain cached"
            raise
        except Exception as e:
            outcome = "Sync failed · See message above; you can try again"
            await self.say(f"Sync failed: {self.config.error(e)}")
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
            if self.demo:
                await self.say("Demo never calls a model. Quit and run canvas-buddy to configure your provider.")
                return
            parts = arg.split(maxsplit=1)
            if not parts or parts[0] not in {"codex", "opencode", "ollama"}:
                await self.say("Usage: /provider codex|opencode|ollama [model]")
                return
            if parts[0] == "ollama" and len(parts) == 1:
                await self.say("Ollama needs a model name: /provider ollama MODEL. Run ollama list to see installed models.")
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
            await self.say(f"Could not search: {self.config.error(e)}")
        finally:
            self.finish_work(outcome)

    @work(group="operation", exclusive=True)
    async def ask(self, question):
        self.begin_work("Searching your courses…")
        outcome = "Ready · Reply below or choose a class above"
        try:
            await self.say("**You:** " + question, role="user")
            if self.demo:
                hits = await self.db.search(question, self.config, self.course, limit=3)
                result = "**Sample search results · no AI model called**\n\n" + (
                    "\n\n".join(f"**{h['title']}**\n\n{h['text']}" for h in hits)
                    or "No matches. Try attendance, exam, or office hours.")
            else:
                result, _ = await answer(self.config, self.db, question, self.course, self.history,
                                         progress=self.set_status)
            label = "Canvas Buddy · sample data" if self.demo else "Canvas Buddy · AI answer"
            await self.say(f"**{label}:**\n\n" + result, role="assistant")
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
            await self.say(f"Could not answer: {self.config.error(e)}")
        finally:
            self.finish_work(outcome)
