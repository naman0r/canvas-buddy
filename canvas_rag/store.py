import hashlib
from array import array
from math import sqrt
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

STALE_HOURS = 6  # Auto-sync threshold shared by the TUI and `sync --if-stale`.


class Store:
    def __init__(self, home):
        path = home / "canvas.sqlite3"
        self.conn = sqlite3.connect(path)
        path.chmod(0o600)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS documents(
                id TEXT PRIMARY KEY, course INTEGER, kind TEXT, title TEXT, url TEXT,
                body TEXT, raw TEXT, hash TEXT, synced TEXT);
            CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY, doc TEXT REFERENCES documents(id) ON DELETE CASCADE,
                text TEXT, vector BLOB, model TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(text, content='chunks', content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS chunk_insert AFTER INSERT ON chunks BEGIN
                INSERT INTO search(rowid,text) VALUES(new.id,new.text); END;
            CREATE TRIGGER IF NOT EXISTS chunk_delete AFTER DELETE ON chunks BEGIN
                INSERT INTO search(search,rowid,text) VALUES('delete',old.id,old.text); END;
            CREATE TABLE IF NOT EXISTS coverage(
                course INTEGER, kind TEXT, state TEXT, detail TEXT, attempted TEXT,
                PRIMARY KEY(course,kind));
            CREATE TABLE IF NOT EXISTS changes(
                course INTEGER, kind TEXT, change TEXT, title TEXT, detail TEXT, at TEXT);
        """)

    def close(self):
        self.conn.close()

    def check_identity(self, url, user):
        identity = json.dumps([url, user])
        old = self.conn.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
        if old and old[0] != identity:
            raise ValueError("Cache belongs to a different Canvas account. Use a new CANVAS_BUDDY_HOME.")
    def bind(self, url, user):
        self.check_identity(url, user)
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES('identity',?)", (json.dumps([url, user]),))

    def prune_courses(self, courses):
        allowed = set(courses) | {0}
        with self.conn:
            for row in self.conn.execute("SELECT DISTINCT course FROM documents").fetchall():
                if row[0] not in allowed:
                    self.conn.execute("DELETE FROM documents WHERE course=?", (row[0],))
                    self.conn.execute("DELETE FROM coverage WHERE course=?", (row[0],))
                    self.conn.execute("DELETE FROM changes WHERE course=?", (row[0],))
            # Remove only deselected conversations; retain selected ones if inbox sync fails.
            for row in self.conn.execute("SELECT id,raw FROM documents WHERE kind='inbox'").fetchall():
                if json.loads(row["raw"]).get("context_code") not in {f"course_{i}" for i in courses}:
                    self.conn.execute("DELETE FROM documents WHERE id=?", (row["id"],))

    # The change log is a rolling window (see CHANGE_DAYS) rather than "since the previous sync":
    # syncing twice in a morning must not hide an announcement, and a failed sync must not erase it.
    CHANGE_DAYS = 7

    def change(self, course, kind, change, title, detail=""):
        with self.conn:
            self.conn.execute("INSERT INTO changes VALUES(?,?,?,?,?,?)",
                              (course, kind, change, title, detail, datetime.now(timezone.utc).isoformat()))

    def prune_changes(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.CHANGE_DAYS)).isoformat()
        with self.conn:
            self.conn.execute("DELETE FROM changes WHERE at < ?", (cutoff,))

    def changes(self, course=None):
        return self.conn.execute(
            "SELECT * FROM changes WHERE (? IS NULL OR course=?) ORDER BY rowid DESC LIMIT 60",
            (course, course)).fetchall()

    def mark_synced(self):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES('last_sync',?)",
                              (datetime.now(timezone.utc).isoformat(),))

    def stale(self, hours=STALE_HOURS):
        row = self.conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
        # Caches from before last_sync existed fall back to the newest document timestamp.
        last = row[0] if row else self.conn.execute("SELECT max(synced) FROM documents").fetchone()[0]
        if not last:
            return False
        return datetime.fromisoformat(last) < datetime.now(timezone.utc) - timedelta(hours=hours)

    def replace(self, course, kind, records):
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            # Only a course/kind that was synced before can have "new" or "removed" entries;
            # otherwise the first sync would log every cached document.
            prior = self.conn.execute("SELECT 1 FROM coverage WHERE course=? AND kind=? AND state!='error'",
                                      (course, kind)).fetchone()
            label = self._course_name(course) if kind == "grade" else None
            ids = {r["id"] for r in records}
            for row in self.conn.execute("SELECT id,title FROM documents WHERE course=? AND kind=?", (course, kind)).fetchall():
                if row[0] not in ids:
                    if prior:
                        self.conn.execute("INSERT INTO changes VALUES(?,?,?,?,?,?)",
                                          (course, kind, "removed", label or row["title"], "", now))
                    self.conn.execute("DELETE FROM documents WHERE id=?", (row[0],))
            for r in records:
                digest = hashlib.sha256((r["title"] + "\n" + r["body"]).encode()).hexdigest()
                old = self.conn.execute("SELECT * FROM documents WHERE id=?", (r["id"],)).fetchone()
                self.conn.execute("""INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,
                    body=excluded.body,raw=excluded.raw,hash=excluded.hash,synced=excluded.synced""",
                    (r["id"], course, kind, r["title"], r["url"], r["body"], json.dumps(r["raw"]), digest, now))
                if not old and prior:
                    self.conn.execute("INSERT INTO changes VALUES(?,?,?,?,?,?)",
                                      (course, kind, "new", label or r["title"], "", now))
                elif old and old["hash"] != digest:
                    before, after = json.loads(old["raw"]), r.get("raw") or {}
                    detail = self._change_detail(kind, before, after, old["title"], r["title"])
                    # Grades only count when a score moved; other kinds when non-activity content did.
                    if detail or (kind != "grade" and self._signature(before) != self._signature(after)):
                        self.conn.execute("INSERT INTO changes VALUES(?,?,?,?,?,?)",
                                          (course, kind, "changed", label or r["title"], detail, now))
                if not old or old["hash"] != digest:
                    self.conn.execute("DELETE FROM chunks WHERE doc=?", (r["id"],))
                    words = r["body"].split()
                    for start in range(0, max(1, len(words)), 280):
                        text = r["title"] + "\n" + " ".join(words[start:start + 340])
                        self.conn.execute("INSERT INTO chunks(doc,text) VALUES(?,?)", (r["id"], text))

    # Fields Canvas rewrites whenever the student merely views something.
    VOLATILE = {"last_activity_at", "total_activity_time", "last_attended_at", "completed_at", "state",
                "read_state", "unread_count", "completion_requirement", "todo_date", "updated_at"}

    @classmethod
    def _signature(cls, value):
        if isinstance(value, dict):
            return {k: cls._signature(v) for k, v in value.items() if k not in cls.VOLATILE}
        if isinstance(value, list):
            return [cls._signature(v) for v in value]
        return value

    @staticmethod
    def when(value):
        """Local short timestamp for any Canvas ISO string, or None when absent or malformed."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%b %d, %H:%M")
        except ValueError:
            return None

    def _course_name(self, course):
        row = self.conn.execute("SELECT title FROM documents WHERE course=? AND kind='course'", (course,)).fetchone()
        return row[0] if row else f"course {course}"

    @classmethod
    def _change_detail(cls, kind, old, new, old_title, new_title):
        parts = []
        if kind == "grade":
            before = (old.get("grades") or {}).get("current_score")
            after = (new.get("grades") or {}).get("current_score")
            if before != after:
                parts.append(f"current score {before if before is None else round(before, 2)} → "
                             f"{after if after is None else round(after, 2)}")
        else:
            before = cls.when(old.get("due_at") if "due_at" in old else old.get("start_at"))
            after = cls.when(new.get("due_at") if "due_at" in new else new.get("start_at"))
            if before != after and after:
                parts.append(f"deadline moved from {before} to {after}" if before else f"deadline set for {after}")
        if old_title != new_title:
            parts.append(f"retitled from {old_title!r}")
        return "; ".join(parts)

    def coverage(self, course, kind, state, detail):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO coverage VALUES(?,?,?,?,?)",
                              (course, kind, state, detail, datetime.now(timezone.utc).isoformat()))

    def get(self, id):
        row = self.conn.execute("SELECT * FROM documents WHERE id=?", (id,)).fetchone()
        return self.decode(row) if row else None

    @staticmethod
    def decode(row):
        d = dict(row)
        d["raw"] = json.loads(d["raw"])
        return d

    def documents(self, course=None, kind=None):
        return [self.decode(r) for r in self.conn.execute(
            "SELECT * FROM documents WHERE (? IS NULL OR course=?) AND (? IS NULL OR kind=?) ORDER BY title",
            (course, course, kind, kind))]

    def status(self):
        counts = self.conn.execute("SELECT count(*),sum(vector IS NOT NULL) FROM chunks").fetchone()
        lines = [f"{len(self.documents())} documents · {counts[0]} chunks · {counts[1] or 0} embedded"]
        lines += [f"{r['course']} · {r['kind']}: {r['state']} — {r['detail']} ({r['attempted']})"
                  for r in self.conn.execute("SELECT * FROM coverage ORDER BY course,kind")]
        skipped = [d for d in self.documents(kind="file") if any(t in d["body"] for t in
                   ("[Content not extracted:", "[Extraction failed:", "[No extractable text;"))]
        lines += [f"File text unavailable: {d['title']}" for d in skipped]
        return "\n".join(lines)

    def snapshot_summary(self):
        count, oldest = self.conn.execute("SELECT count(*),min(synced) FROM documents").fetchone()
        issues = self.conn.execute("SELECT count(*) FROM coverage WHERE state != 'ok'").fetchone()[0]
        files = sum(any(marker in d["body"] for marker in
                        ("[Content not extracted:", "[Extraction failed:", "[No extractable text;", "[Content unavailable:"))
                    for d in self.documents(kind="file"))
        if not count:
            return "No cached documents · Choose courses, then Sync"
        date = datetime.fromisoformat(oldest).astimezone().strftime("%b %d, %H:%M %Z")
        detail = f" · {issues} incomplete sections, {files} unreadable files" if issues or files else ""
        return f"{count} documents · Oldest synced {date}{detail} · /status for details"

    async def embed(self, config, progress=lambda s: None):
        if not config.embed_model:
            self.coverage(0, "embeddings", "ok", "Disabled; using keyword search")
            return
        rows = self.conn.execute("SELECT id,text FROM chunks WHERE vector IS NULL OR model!=?",
                                 (config.embed_model,)).fetchall()
        try:
            config.validate_ollama()
            async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
                for offset in range(0, len(rows), 24):
                    batch = rows[offset:offset + 24]
                    r = await client.post(config.ollama + "/api/embed", json={"model": config.embed_model,
                        "input": ["search_document: " + x["text"] for x in batch], "truncate": True})
                    r.raise_for_status()
                    vectors = r.json()["embeddings"]
                    if len(vectors) != len(batch):
                        raise ValueError("Wrong embedding count")
                    with self.conn:
                        for row, vec in zip(batch, vectors):
                            self.conn.execute("UPDATE chunks SET vector=?,model=? WHERE id=?",
                                (array("f", vec).tobytes(), config.embed_model, row["id"]))
                    progress(f"Embedded {min(offset + 24, len(rows))}/{len(rows)} changed chunks")
            self.coverage(0, "embeddings", "ok", f"{len(rows)} chunks updated")
        except (httpx.HTTPError, ValueError, KeyError):
            self.coverage(0, "embeddings", "error", "Ollama unavailable/model missing; keyword search still works")

    async def search(self, question, config, course=None, limit=10):
        words = re.findall(r"\w+", question.lower())
        stop = {"what", "when", "where", "which", "the", "a", "an", "is", "are", "my", "did", "do",
                "about", "does", "have", "any", "there", "for", "of", "in", "to", "and", "it", "say"}
        words = list(dict.fromkeys(w for w in words if w not in stop))[:32]
        scores, hits = {}, {}
        if words:
            match = " OR ".join('"' + w + '"' for w in words)
            rows = self.conn.execute("""SELECT c.id,c.text,d.* FROM search
                JOIN chunks c ON c.id=search.rowid JOIN documents d ON d.id=c.doc
                WHERE search MATCH ? AND (? IS NULL OR d.course=?) ORDER BY bm25(search) LIMIT 60""",
                (match, course, course)).fetchall()
            for rank, row in enumerate(rows):
                key = row[0]
                hits[key] = dict(row)
                scores[key] = 1 / (30 + rank)
        if config.embed_model:
            try:
                config.validate_ollama()
                async with httpx.AsyncClient(timeout=12, trust_env=False) as client:
                    r = await client.post(config.ollama + "/api/embed", json={"model": config.embed_model,
                        "input": "search_query: " + question})
                    r.raise_for_status()
                    q = r.json()["embeddings"][0]
                    qnorm = sqrt(sum(x * x for x in q))
                rows = self.conn.execute("""SELECT c.id,c.text,c.vector,d.* FROM chunks c
                    JOIN documents d ON d.id=c.doc WHERE c.model=? AND c.vector IS NOT NULL
                    AND (? IS NULL OR d.course=?)""", (config.embed_model, course, course)).fetchall()
                # Pure-Python cosine over every chunk is linear in cache size: instant for a few
                # hundred chunks, a second or two at ten thousand. Not worth a NumPy dependency yet.
                ranked = []
                for row in rows:
                    v = array("f", row["vector"])
                    if len(v) == len(q):
                        similarity = sum(x * y for x, y in zip(v, q)) / max(sqrt(sum(x * x for x in v)) * qnorm, 1e-9)
                        ranked.append((similarity, row))
                for rank, (_, row) in enumerate(sorted(ranked, key=lambda x: x[0], reverse=True)[:60]):
                    key = row[0]
                    hits[key] = dict(row)
                    scores[key] = scores.get(key, 0) + 1 / (30 + rank)
            except (httpx.HTTPError, ValueError, KeyError):
                pass
        out, per_doc = [], {}
        for key in sorted(scores, key=scores.get, reverse=True):
            hit = hits[key]
            # SQLite duplicate 'id' column refers to chunk id; group by source URL/title.
            doc = (hit["url"], hit["title"])
            if per_doc.get(doc, 0) >= 2:
                continue
            per_doc[doc] = per_doc.get(doc, 0) + 1
            out.append(hit)
            if len(out) == limit:
                break
        return out

    def upcoming(self, course=None, days=30, overdue=False):
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        out = []
        for d in self.documents(course):
            if d["kind"] not in {"assignment", "event"}:
                continue
            raw = d["raw"]
            due = raw.get("due_at") if d["kind"] == "assignment" else raw.get("start_at")
            if not due:
                continue
            try:
                date = datetime.fromisoformat(due.replace("Z", "+00:00"))
            except ValueError:
                continue
            submission = raw.get("submission") or {}
            complete = submission.get("workflow_state") in {"submitted", "graded", "pending_review"} or submission.get("excused")
            if (overdue and date < now and not complete) or (not overdue and now <= date <= end):
                out.append({**d, "due": date.astimezone().isoformat(),
                            "state": submission.get("workflow_state", "unknown")})
        return sorted(out, key=lambda x: x["due"])

    def grades(self, course=None):
        names = {d["course"]: d["title"] for d in self.documents(kind="course")}
        lines = []
        for d in self.documents(course, "grade"):
            grades = d["raw"].get("grades") or {}
            def value(key):
                v = grades.get(key)
                return "not posted" if v is None else str(v)
            lines.append(f"{names.get(d['course'], d['course'])}\n"
                         f"Current: {value('current_score')} / {value('current_grade')} · "
                         f"Final: {value('final_score')} / {value('final_grade')}\n"
                         f"Synced {d['synced']}\n{d['url']}")
        return "\n\n".join(lines) or "No grade records cached. /status shows coverage."

    def changes_markdown(self, course=None):
        log = self.changes(course)
        label = {"new": "New", "changed": "Changed", "removed": "Removed"}
        return f"**Recent changes** (last {self.CHANGE_DAYS} days; {len(log)} recorded)\n" + ("\n".join(
            f"- {self.when(c['at'])} · {label.get(c['change'], c['change'])} · {c['kind']} · {c['title'][:70]}"
            + (f" · {c['detail']}" if c["detail"] else "") for c in log)
            or "None recorded yet. After your next sync, this lists what Canvas posted, moved, or removed.")

    def dashboard(self, course=None, days=14):
        sections = []
        rows = self.upcoming(course, days=days)
        sections.append(f"**Upcoming work** (next {days} days; local timezone)\n" + ("\n".join(
            f"- {self.when(r['due'])} · [{r['title']}]({r['url']}) · {r['state']}" for r in rows)
            or "Nothing dated in the cache for this window. Try /upcoming for 30 days."))
        sections.append(self.changes_markdown(course))
        grades = []
        names = {d["course"]: d["title"] for d in self.documents(kind="course")}
        for g in self.documents(course, "grade"):
            score = (g["raw"].get("grades") or {}).get("current_score", None)
            grades.append(f"- {names.get(g['course'], g['course'])}: "
                          + ("no grade posted" if score is None else f"{round(score, 2)}"))
        sections.append("**Grades**\n" + ("\n".join(grades) or "No grade records cached; grade sync needs coverage. /status shows details."))
        return "\n\n".join(sections)

    def facts(self, course=None):
        lines = []
        for d in self.documents(course):
            r = d["raw"]
            if d["kind"] == "course":
                lines.append(f"Course {d['course']}: {d['title']}")
            elif d["kind"] in {"grade", "weight"}:
                lines.append(f"{d['course']} {d['kind']}: {d['body'][:1800]}")
            elif d["kind"] == "assignment":
                s = r.get("submission") or {}
                lines.append(f"{d['course']} | {d['title']} | due={r.get('due_at')} | "
                             f"status={s.get('workflow_state')} | score={s.get('score')}/{r.get('points_possible')} | "
                             f"missing={s.get('missing')} late={s.get('late')} | {d['url']}")
            elif d["kind"] == "event":
                lines.append(f"{d['course']} event {d['title']} | {r.get('start_at')} | {d['url']}")
            elif d["kind"] == "todo":
                lines.append(f"{d['course']} todo ({r.get('type')}) {d['title']} | "
                             f"due={(r.get('assignment') or r.get('quiz') or {}).get('due_at')} | {d['url']}")
        text = "\n".join(lines)
        return text[:18000] + ("\n[Structured snapshot truncated; narrow the course filter.]" if len(text) > 18000 else "")
