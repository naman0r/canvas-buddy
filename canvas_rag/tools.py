"""Read-only lookups over the local cache, offered to the answering model as tools.

Codex and OpenCode reach them through a stdio MCP server (`python -m canvas_rag.tools`) that
the CLI spawns; Ollama calls them in-process. Every tool reads SQLite and nothing else: no Canvas
requests, no writes, no file access outside the cache. Course documents are untrusted, so a tool
must never do more than a curious student could do by reading the cache themselves.
"""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from .config import Config
from .store import MARKER, Store

PART_CHARS = 8000

TOOLS = [
    {"name": "search", "description": "Search every cached Canvas document (syllabi, pages, files, slides, "
     "announcements, assignments, discussions) and the course websites the student added. Returns ranked "
     "excerpts with document ids.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "What to look for, in your own words"},
         "course": {"type": "integer", "description": "Optional course id from list_courses"}},
         "required": ["query"]}},
    {"name": "read_document", "description": "Read the full cached text of one document by id. Long "
     "documents come in numbered parts of about 8000 characters; PDF pages and slides are marked.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "part": {"type": "integer", "description": "Part number, from 1"}},
         "required": ["id"]}},
    {"name": "list_documents", "description": "List cached documents with ids, optionally filtered by "
     "course, type (page, file, site, assignment, announcement, discussion, module, quiz, event, submission) "
     "or words in the title. Use it to find a file or module the search missed.",
     "inputSchema": {"type": "object", "properties": {
         "course": {"type": "integer"}, "kind": {"type": "string"}, "title": {"type": "string"}}}},
    {"name": "list_courses", "description": "The student's courses: ids, names, teaching staff, grade "
     "weights, current Canvas grade and any sections that failed to sync.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "deadlines", "description": "Dated assignments and events with the student's submission "
     "status, sorted by date. Set overdue to list past-due unsubmitted work instead.",
     "inputSchema": {"type": "object", "properties": {
         "days": {"type": "integer", "description": "Window ahead, default 14"},
         "course": {"type": "integer"}, "overdue": {"type": "boolean"}}}},
    {"name": "recent_changes", "description": "What Canvas posted, moved or removed in the last 7 days.",
     "inputSchema": {"type": "object", "properties": {"course": {"type": "integer"}}}},
]


class Lookup:
    """Tool calls against one cache. `course` pins every call to the student's course filter."""

    def __init__(self, db, config, course=None):
        self.db, self.config, self.course = db, config, course

    def _course(self, value):
        return self.course or value or None

    def _names(self):
        return self.db.course_names()

    def allowed(self, doc):
        return doc and (not self.course or doc["course"] in (self.course, 0))

    async def call(self, name, args):
        args = args or {}
        if name == "search":
            hits = await self.db.search(str(args.get("query", ""))[:500], self.config,
                                        self._course(args.get("course")), limit=8)
            return "\n\n".join(f"[{h['doc']}] {h['url']}\n{h['text']}" for h in hits) or "No matching cached text."
        if name == "read_document":
            doc = self.db.get(str(args.get("id", "")))
            if not self.allowed(doc):
                return "No cached document with that id. Use search or list_documents to find ids."
            parts = split(doc["body"])
            n = min(max(int(args.get("part") or 1), 1), len(parts))
            return (f"{doc['title']} ({doc['kind']}, {self._names().get(doc['course'], 'Inbox')})\n{doc['url']}\n"
                    f"Synced {doc['synced']} · part {n} of {len(parts)}\n\n{parts[n - 1]}")
        if name == "list_documents":
            words = str(args.get("title") or "").casefold().split()
            docs = [d for d in self.db.documents(self._course(args.get("course")), args.get("kind") or None)
                    if all(w in d["title"].casefold() for w in words)]
            names = self._names()
            lines = [f"[{d['id']}] {d['kind']} · {d['title']} · {names.get(d['course'], 'Inbox')}"
                     + (f" · due {d['raw']['due_at']}" if d["raw"].get("due_at") else "") for d in docs[:120]]
            more = f"\n… {len(docs) - 120} more; narrow the filter." if len(docs) > 120 else ""
            return "\n".join(lines) + more if lines else "No matching documents."
        if name == "list_courses":
            out = []
            for cid, title in self._names().items():
                if self.course and cid != self.course:
                    continue
                staff = ", ".join(d["title"] for d in self.db.documents(cid, "teacher"))
                weights = "; ".join(f"{d['title']} {d['raw'].get('group_weight')}%"
                                    for d in self.db.documents(cid, "weight") if d["raw"].get("group_weight"))
                grade = next((d["body"].replace("\n", "; ") for d in self.db.documents(cid, "grade")), "not synced")
                gaps = [f"{r['kind']} ({r['state']})" for r in self.db.conn.execute(
                    "SELECT kind,state FROM coverage WHERE course=? AND state!='ok'", (cid,))]
                extra = self.db.context().get(cid, {"notes": "", "sites": []})
                out.append(f"Course {cid}: {title}\nStaff: {staff or 'unknown'}\nWeights: {weights or 'not published'}"
                           f"\nGrade: {grade}" + (f"\nIncomplete sync: {', '.join(gaps)}" if gaps else "")
                           + (f"\nStudent's notes: {extra['notes']}" if extra["notes"] else "")
                           + (f"\nCourse websites (cached as kind 'site'): {', '.join(extra['sites'])}"
                              if extra["sites"] else ""))
            return "\n\n".join(out) or "No courses cached."
        if name == "deadlines":
            rows = self.db.upcoming(self._course(args.get("course")), days=int(args.get("days") or 14),
                                    overdue=bool(args.get("overdue")))
            names = self._names()
            return "\n".join(f"{r['due']} · {r['title']} ({r['kind']}) · {names.get(r['course'], r['course'])} · "
                             f"status {r['state']} · [{r['id']}] {r['url']}" for r in rows) or "Nothing dated in that window."
        if name == "recent_changes":
            return self.db.changes_markdown(self._course(args.get("course")))
        raise ValueError(f"Unknown tool {name}")


def split(body):
    """Cut text into parts of at most PART_CHARS on line boundaries, preferring to start a part at a
    page or slide marker so "part 3" begins where a page does."""
    parts, current = [], ""
    # PDF text can arrive as one enormous line; cut such lines so no part exceeds the budget.
    lines = (text[i:i + PART_CHARS] for text in body.splitlines(keepends=True) for i in range(0, len(text), PART_CHARS))
    for line in lines:
        full = len(current) + len(line) > PART_CHARS
        if current and (full or (MARKER.fullmatch(line.strip()) and len(current) > PART_CHARS // 2)):
            parts.append(current)
            current = ""
        current += line
    return parts + [current] if current or not parts else parts


class ReadOnlyStore(Store):
    """The Store's queries over a connection SQLite itself refuses to write through."""

    def __init__(self, home):
        self.conn = sqlite3.connect(f"file:{Path(home) / 'canvas.sqlite3'}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row


def serve(home, course, embed_model, ollama):
    """MCP over stdio: newline-delimited JSON-RPC, the four methods a tool-only server needs."""
    lookup = Lookup(ReadOnlyStore(home), Config(home=Path(home), embed_model=embed_model, ollama=ollama), course)
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue  # Notifications such as notifications/initialized need no reply.
        method, params = message.get("method"), message.get("params") or {}
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        if method == "initialize":
            reply["result"] = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                               "capabilities": {"tools": {}},
                               "serverInfo": {"name": "canvas-buddy", "version": "1"}}
        elif method == "tools/list":
            reply["result"] = {"tools": [{**t, "annotations": {"readOnlyHint": True, "openWorldHint": False}}
                                         for t in TOOLS]}
        elif method == "tools/call":
            try:
                text = asyncio.run(lookup.call(params.get("name"), params.get("arguments")))
                reply["result"] = {"content": [{"type": "text", "text": text}]}
            except (ValueError, TypeError, sqlite3.Error) as e:
                reply["result"] = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        elif method == "ping":
            reply["result"] = {}
        else:
            reply["error"] = {"code": -32601, "message": f"Unsupported method {method}"}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    home, course, embed_model, ollama = sys.argv[1:5]
    serve(home, int(course) or None, embed_model, ollama)
