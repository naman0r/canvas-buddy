"""The Library: cached Canvas content arranged the way each course is, with a reader for any document."""
import re

from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, MarkdownViewer, Static, Tree

from .store import MARKER

# Reading order within a course. Grades, weights, staff and to-do rows are facts, not reading.
SECTIONS = [("announcement", "Announcements"), ("module", "Modules"), ("assignment", "Assignments"),
            ("quiz", "Quizzes"), ("discussion", "Discussions"), ("page", "Pages"), ("file", "Files"),
            ("submission", "Feedback"), ("event", "Events")]
MODULE_KINDS = {"Page": "page", "File": "file", "Assignment": "assignment", "Quiz": "quiz",
                "Discussion": "discussion"}


def module_items(module, docs):
    """(label, doc id or None, indent) for each module item, linked to the cached document it names."""
    course = module["course"]
    by_title = {(d["kind"], d["title"]): d["id"] for d in docs}
    known = {d["id"] for d in docs}
    out = []
    for item in module["raw"].get("items") or []:
        title = " ".join((item.get("title") or "").split())
        kind = MODULE_KINDS.get(item.get("type"))
        key = item.get("page_url") if kind == "page" else item.get("content_id")
        doc = f"{course}:{kind}:{key}" if kind and key else None
        if doc not in known:
            doc = by_title.get((kind, title))
        out.append((title, doc, item.get("indent") or 0, item.get("type")))
    return out


def reading(doc, course_name):
    """A document as Markdown: page/slide markers become headings the reader can jump to, and
    consecutive lines stay one block with hard breaks, as they were laid out on the page."""
    blocks, run, kind = [], [], None

    def flush():
        if run:
            blocks.append(("\n" if kind == "list" else "\\\n").join(run))
            run.clear()
    for line in doc["body"].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if MARKER.fullmatch(stripped) or stripped.startswith("## "):
            flush()
            blocks.append(f"### {stripped}" if MARKER.fullmatch(stripped)
                          else "## " + re.sub(r"([\\`*_{}\[\]<>|])", r"\\\1", stripped[3:]))
            continue
        item = re.match(r"(\s*)- (.*)", line)
        if (kind == "list") != bool(item):
            flush()
        kind = "list" if item else "text"
        text = item.group(2) if item else stripped
        text = re.sub(r"([\\`*_{}\[\]<>#+|~])", r"\\\1", text)
        run.append(f"{item.group(1)}- {text}" if item else text)
    flush()
    meta = f"{course_name} · {doc['kind']} · synced {doc['synced'][:16].replace('T', ' ')} UTC"
    return f"# {doc['title']}\n\n_{meta}_\n\n" + "\n\n".join(blocks)


def anchor(location):
    """'Slide 4–6' → 'slide-4', the slug Textual gives the '### Slide 4' heading. Slugs pass through."""
    return location.split("–")[0].strip().lower().replace(" ", "-") if location else None


class Library(ModalScreen):
    BINDINGS = [("escape", "close", "Close"), ("o", "open_source", "Open in Canvas"),
                ("a", "ask", "Ask about this"), ("slash", "filter", "Filter")]
    CSS = """
    Library { align: center middle; }
    #library { width: 98%; height: 96%; padding: 0 1; border: round $accent; background: $surface; }
    #library-top { height: 3; }
    #library-title { width: auto; padding: 1 1 0 0; text-style: bold; }
    #doc-filter { width: 1fr; }
    #library-body { height: 1fr; }
    #doc-tree { width: 34%; min-width: 28; border-right: tall $panel; }
    #doc-view MarkdownTableOfContents { width: 22; max-width: 22; }
    #reader { width: 1fr; }
    #doc-view { height: 1fr; }
    #doc-hint { height: auto; padding: 0 1; color: $text-muted; }
    .library-buttons { height: 3; }
    """

    def __init__(self, db, course=None, doc=None, location=None):
        super().__init__()
        self.db, self.course = db, course
        self.docs = {d["id"]: d for d in db.documents(course)}
        self.names = db.course_names()
        self.start, self.location, self.current = doc, location, None

    def compose(self):
        with Vertical(id="library"):
            with Horizontal(id="library-top"):
                yield Label("Library", id="library-title")
                yield Input(placeholder="Filter by title…  (/ to focus, Esc closes)", id="doc-filter")
            with Horizontal(id="library-body"):
                yield Tree("Courses", id="doc-tree")
                with Vertical(id="reader"):
                    yield Static("Choose a document on the left. Enter opens it; o opens it in Canvas; "
                                 "a asks about it.", id="doc-hint", markup=False)
                    yield MarkdownViewer("", id="doc-view", show_table_of_contents=False, open_links=False)
            with Horizontal(classes="library-buttons"):
                yield Button("Open in Canvas", id="open-source", disabled=True)
                yield Button("Ask about this", id="ask-doc", variant="primary", disabled=True)
                yield Button("Close", id="close")

    def on_mount(self):
        self.build()
        tree = self.query_one("#doc-tree", Tree)
        if self.start in self.docs:
            self.show(self.start, self.location)
            # Reveal where the document lives in its course, like a file manager does.
            stack = list(tree.root.children)
            while stack:
                node = stack.pop()
                if node.data == self.start:
                    parent = node.parent
                    while parent:
                        parent.expand()
                        parent = parent.parent
                    self.call_after_refresh(tree.move_cursor, node)
                    break
                stack += node.children
        tree.focus()

    def build(self, words=()):
        tree = self.query_one("#doc-tree", Tree)
        tree.clear()
        tree.show_root = False
        docs = list(self.docs.values())

        def matches(d):
            return all(w in d["title"].casefold() for w in words)
        for course in sorted({d["course"] for d in docs}):
            node = tree.root.add(Text(self.names.get(course, "Inbox"), style="bold"), expand=bool(words)
                                 or len({d["course"] for d in docs}) == 1)
            own = [d for d in docs if d["course"] == course]
            syllabus = next((d for d in own if d["kind"] == "course" and "## Syllabus" in d["body"]), None)
            if syllabus and matches({"title": "Syllabus " + syllabus["title"]}):
                node.add_leaf("Syllabus", data=syllabus["id"])
            for kind, label in SECTIONS + ([("inbox", "Messages")] if course == 0 else []):
                rows = [d for d in own if d["kind"] == kind]
                if kind == "module":
                    rows.sort(key=lambda d: d["raw"].get("position") or 0)
                elif kind in {"announcement", "discussion"}:
                    rows.sort(key=lambda d: d["raw"].get("posted_at") or "", reverse=True)
                elif kind in {"assignment", "quiz"}:
                    rows.sort(key=lambda d: d["raw"].get("due_at") or "9999")
                if kind == "module" and not words:
                    if rows:
                        section = node.add(f"{label} ({len(rows)})")
                        for m in rows:
                            branch = section.add(m["title"], data=m["id"])
                            for title, doc, indent, type_ in module_items(m, own):
                                text = Text("  " * indent + title, style="" if doc else "dim")
                                if type_ == "SubHeader":
                                    text.stylize("italic")
                                branch.add_leaf(text, data=doc)
                    continue
                rows = [d for d in rows if matches(d)]
                if rows:
                    section = node.add(f"{label} ({len(rows)})", expand=bool(words))
                    for d in rows:
                        section.add_leaf(d["title"], data=d["id"])
            if not node.children:
                node.remove()
        if not tree.root.children:
            tree.root.add_leaf(Text("Nothing cached matches that filter.", style="dim"))

    @on(Input.Changed, "#doc-filter")
    def filter_documents(self, event):
        self.build(event.value.casefold().split())

    @on(Input.Submitted, "#doc-filter")
    def to_tree(self):
        self.query_one("#doc-tree", Tree).focus()

    @on(Tree.NodeSelected, "#doc-tree")
    def selected(self, event):
        if event.node.data in self.docs:
            self.show(event.node.data)

    def show(self, doc_id, location=None):
        doc = self.docs[doc_id]
        self.current = doc_id
        viewer = self.query_one("#doc-view", MarkdownViewer)
        markers = sum(1 for line in doc["body"].splitlines() if MARKER.fullmatch(line.strip()) or line.startswith("## "))
        viewer.show_table_of_contents = markers >= 3
        self.query_one("#doc-hint", Static).update(doc["url"])
        self.query_one("#open-source", Button).disabled = False
        self.query_one("#ask-doc", Button).disabled = False

        async def load():
            await viewer.document.update(reading(doc, self.names.get(doc["course"], "Inbox")))
            viewer.scroll_home(animate=False)
            if location:
                self.call_after_refresh(viewer.document.goto_anchor, anchor(location))
        self.run_worker(load(), exclusive=True, group="reader")

    def action_filter(self):
        self.query_one("#doc-filter", Input).focus()

    @on(Button.Pressed, "#open-source")
    def action_open_source(self):
        if self.current:
            self.app.open_source(self.docs[self.current]["url"])

    @on(Button.Pressed, "#ask-doc")
    def action_ask(self):
        if self.current:
            self.dismiss(self.current)

    @on(Button.Pressed, "#close")
    def action_close(self):
        self.dismiss(None)
