"""Canvas GET requests only. Never forward the PAT to file hosts or foreign pagination links."""
import asyncio
import io
import fcntl
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader


def plain(value):
    value = str(value) if value is not None else ""
    if "<" not in value:
        return value
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return re.sub(r"[ \t]+", " ", soup.get_text("\n", strip=True)).strip()


def readable(value):
    if isinstance(value, dict):
        return "\n".join(f"{k}: {readable(v)}" for k, v in value.items()
                         if v is not None and k not in {"avatar_image_url", "url", "preview_url"})
    if isinstance(value, list):
        return "\n".join(readable(v) for v in value)
    return plain(value)


def extract(data, name):
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix == "pdf":
        return "\n\n".join(f"Page {i + 1}\n{page.extract_text() or ''}"
                           for i, page in enumerate(PdfReader(io.BytesIO(data)).pages))
    if suffix in {"docx", "pptx", "xlsx"}:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = sorted(n for n in z.namelist() if
                           n == "word/document.xml" or re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
                           or n == "xl/sharedStrings.xml" or re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
            if sum(z.getinfo(n).file_size for n in names) > 80_000_000:
                raise ValueError("Office document expands beyond 80 MB")
            return "\n".join(" ".join(ElementTree.fromstring(z.read(n)).itertext()) for n in names)
    return plain(data.decode("utf-8", errors="replace"))


class Canvas:
    def __init__(self, config, transport=None):
        config.validate_canvas()
        self.base = config.url
        self.token = config.token
        self.client = httpx.AsyncClient(timeout=45, transport=transport)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def get(self, path, params=None):
        url = path if path.startswith("https://") else self.base + "/api/v1/" + path.lstrip("/")
        if urlsplit(url).netloc != urlsplit(self.base).netloc or urlsplit(url).scheme != "https":
            raise ValueError("Canvas returned a foreign API link; refusing to send credentials")
        for attempt in range(4):
            try:
                r = await self.client.get(url, params=params, headers={"Authorization": f"Bearer {self.token}"})
            except httpx.TransportError:
                if attempt == 3:
                    raise RuntimeError("Canvas connection failed; check network and site URL") from None
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                retry = r.headers.get("Retry-After", "")
                await asyncio.sleep(min(float(retry) if retry.isdigit() else 2 ** attempt, 20))
                continue
            if r.status_code >= 300:
                hint = {401: "Token expired or invalid. Create a new token in Canvas Account → Settings, then open Courses setup.",
                        403: "Your account cannot access this resource; your school may restrict it.",
                        404: "Resource unavailable; it may be unpublished, removed, or restricted."}.get(r.status_code, "Try syncing again later.")
                raise RuntimeError(f"Canvas HTTP {r.status_code}: {urlsplit(url).path}. {hint}")
            try:
                r.json()
            except ValueError:
                raise RuntimeError("Canvas returned non-JSON data; check your Canvas site URL") from None
            return r

    async def one(self, path, params=None):
        return (await self.get(path, params)).json()

    async def all(self, path, params=None):
        result, visited = [], set()
        params = {"per_page": 100, **(params or {})}
        while path:
            if path in visited:
                raise RuntimeError("Canvas pagination loop")
            visited.add(path)
            r = await self.get(path, params)
            rows = r.json()
            if not isinstance(rows, list):
                raise RuntimeError("Expected a Canvas list response")
            result.extend(rows)
            path = r.links.get("next", {}).get("url")
            params = None
        return result

    async def courses(self):
        return await self.all("courses", {"include[]": ["term", "total_scores"], "enrollment_type": "student"})

    async def file_text(self, item):
        if item.get("locked_for_user"):
            return "[Content not extracted: locked for this student.]"
        name = item.get("filename", "")
        if name.lower().rsplit(".", 1)[-1] not in {
            "pdf", "docx", "pptx", "xlsx", "txt", "md", "csv", "html", "htm", "tex", "py", "json", "xml"
        }:
            return "[Content not extracted: unsupported format; open the Canvas link.]"
        if item.get("size", 0) > 25_000_000:
            return "[Content not extracted: file exceeds 25 MB.]"
        url = item.get("url", "")
        # Signed Canvas download links do not need the PAT. Follow HTTPS redirects without auth.
        for _ in range(8):
            u = urlsplit(url)
            if u.scheme != "https" or u.username:
                raise ValueError("Unsafe file URL")
            async with self.client.stream("GET", url) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"File download HTTP {r.status_code}")
                data = bytearray()
                async for part in r.aiter_bytes():
                    data.extend(part)
                    if len(data) > 25_000_000:
                        raise ValueError("File exceeds 25 MB")
            text = await asyncio.to_thread(extract, bytes(data), name)
            return text if text.strip() else "[No extractable text; this file may require OCR.]"
        raise RuntimeError("Too many file redirects")


def record(course, kind, item, base, body=None):
    key = str(item.get("id", item.get("url", "self")))
    title = item.get("title") or item.get("name") or item.get("display_name") or item.get("subject") or kind
    url = item.get("html_url") or f"{base}/courses/{course}"
    if kind == "file":
        url = f"{base}/courses/{course}/files/{key}"
    elif kind == "grade":
        url = f"{base}/courses/{course}/grades"
    elif kind == "submission":
        url = f"{base}/courses/{course}/assignments/{key}/submissions/self"
    elif kind == "inbox":
        url = f"{base}/conversations/{key}"
    return {"id": f"{course}:{kind}:{key}", "course": course, "kind": kind, "title": title,
            "url": url, "body": body if body is not None else readable(item), "raw": item}


async def sync(config, db, progress=lambda s: None):
    with (config.home / "sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another Canvas Buddy sync is running. Wait for it to finish.") from None
        await _sync(config, db, progress)


async def _sync(config, db, progress):
    """Replace only complete resource snapshots; failed endpoints retain their previous data."""
    async with Canvas(config) as api:
        me = await api.one("users/self/profile")
        db.bind(config.url, str(me["id"]))
        db.prune_courses(config.courses)
        async def collect(cid, kind, fetch):
            progress(f"{cid or 'Personal'} · {kind}")
            try:
                result = await fetch()
                rows, warnings = result if isinstance(result, tuple) else (result, [])
                db.replace(cid, kind, rows)
                db.coverage(cid, kind, "partial" if warnings else "ok",
                            f"{len(rows)} records" + ("; " + "; ".join(warnings) if warnings else ""))
            except (RuntimeError, ValueError, httpx.HTTPError) as e:
                db.coverage(cid, kind, "error", str(e).replace(config.token, "[redacted]"))
                progress(f"{cid} · {kind}: unavailable (see /status)")

        # Courses sync in parallel; within a course the order matters because pages/files
        # discover content through the modules cached just before them.
        limit = asyncio.Semaphore(SYNC_CONCURRENCY)
        async def sync_course(cid):
            async with limit:
                await _sync_course(api, db, me, cid, collect)
        # TaskGroup cancels the siblings when one course raises; a plain gather would leave them
        # running against the closed client and record bogus coverage errors.
        try:
            async with asyncio.TaskGroup() as group:
                for cid in config.courses:
                    group.create_task(sync_course(cid))
        except ExceptionGroup as group_error:
            raise group_error.exceptions[0] from None

        async def inbox():
            out = []
            for c in await api.all("conversations"):
                if str(c.get("context_code", "")) not in {f"course_{i}" for i in config.courses}:
                    continue
                full = await api.one(f"conversations/{c['id']}", {"auto_mark_as_read": "false"})
                out.append(record(0, "inbox", full, api.base))
            return out
        await collect(0, "inbox", inbox)
    progress("Indexing new and changed text…")
    await db.embed(config, progress)
    db.prune_changes()
    db.mark_synced()
    progress("Sync complete. /status shows coverage and any unavailable content.")


SYNC_CONCURRENCY = 3  # Canvas throttles per token; three courses at once stays well under it.


async def _sync_course(api, db, me, cid, collect):
    prefix = f"courses/{cid}"
    async def course():
        c = await api.one(prefix, {"include[]": ["syllabus_body", "total_scores", "term"]})
        return [record(cid, "course", c, api.base)]
    await collect(cid, "course", course)
    for kind, endpoint, params in [
        ("assignment", "assignments", {"include[]": ["submission"]}),
        ("grade", "enrollments", {"user_id": me["id"], "type[]": "StudentEnrollment"}),
        ("weight", "assignment_groups", {}),
        ("announcement", "discussion_topics", {"only_announcements": "true"}),
        ("quiz", "quizzes", {}),
        ("teacher", "users", {"enrollment_type[]": ["teacher", "ta"]}),
    ]:
        async def fetch(endpoint=endpoint, params=params, kind=kind):
            items = await api.all(f"{prefix}/{endpoint}", params)
            return [record(cid, kind, i, api.base) for i in items]
        await collect(cid, kind, fetch)

    async def submissions():
        items = await api.all(f"{prefix}/students/submissions", {
            "student_ids[]": [me["id"]], "include[]": ["submission_comments", "rubric_assessment"]})
        return [record(cid, "submission", {**i, "id": i["assignment_id"],
                "title": f"Submission / feedback for assignment {i['assignment_id']}"}, api.base) for i in items]
    await collect(cid, "submission", submissions)

    async def modules():
        out = []
        for m in await api.all(f"{prefix}/modules"):
            m["items"] = await api.all(f"{prefix}/modules/{m['id']}/items")
            out.append(record(cid, "module", m, api.base))
        return out
    await collect(cid, "module", modules)

    async def discussions():
        out = []
        for d in await api.all(f"{prefix}/discussion_topics"):
            d["thread"] = await api.one(f"{prefix}/discussion_topics/{d['id']}/view")
            out.append(record(cid, "discussion", d, api.base))
        return out
    await collect(cid, "discussion", discussions)

    async def linked_items(kind, endpoint):
        warnings = []
        try:
            items = await api.all(f"{prefix}/{endpoint}")
        except RuntimeError as e:
            if not any(f"HTTP {code}" in str(e) for code in (403, 404)):
                raise
            items = []
            warnings.append(f"Listing unavailable; module/content links only ({e})")
        refs = {str(i.get("url") if kind == "page" else i["id"]): i for i in items}
        for m in db.documents(cid, "module"):
            for i in m["raw"].get("items", []):
                key = i.get("page_url") if kind == "page" else i.get("content_id")
                if i.get("type", "").lower() == kind and key:
                    refs.setdefault(str(key), {"title": i.get("title"), "id": key})
        # HTML can link files absent from the Files tab, including the syllabus.
        pattern = rf"/courses/{cid}/pages/([^\s\"<>?#]+)" if kind == "page" else r"/files/(\d+)"
        for d in db.documents(cid):
            for key in re.findall(pattern, json.dumps(d["raw"])):
                key = unquote(key).rstrip("\\")
                refs.setdefault(key, {"id": key, "title": f"{kind} {key}"})
        out = []
        if kind == "page":
            try:
                front = await api.one(f"{prefix}/front_page")
                refs.setdefault(front["url"], front)
            except RuntimeError:
                pass
        for key, item in refs.items():
            path = f"{prefix}/pages/{quote(key, safe='')}" if kind == "page" else f"files/{key}"
            try:
                item = await api.one(path)
                if kind == "page":
                    out.append(record(cid, kind, item, api.base))
                    continue
                old = db.get(f"{cid}:file:{key}")
                unchanged = old and all(old["raw"].get(k) == item.get(k)
                                        for k in ("updated_at", "size", "locked_for_user"))
                if unchanged and "[Extraction failed:" not in old["body"]:
                    body = old["body"]
                else:
                    body = readable({k: item.get(k) for k in ("display_name", "content-type", "size")})
                    try:
                        body += "\n\n" + await api.file_text(item)
                    except Exception:
                        body += "\n[Extraction failed: open the Canvas link.]"
                out.append(record(cid, kind, item, api.base, body))
            except RuntimeError as e:
                warnings.append(str(e))
                out.append(record(cid, kind, {**item, "id": key}, api.base,
                                  f"[Content unavailable: {e}]"))
        return out, warnings

    await collect(cid, "page", lambda: linked_items("page", "pages"))
    await collect(cid, "file", lambda: linked_items("file", "files"))

    async def calendar():
        now = datetime.now(timezone.utc)
        items = await api.all("calendar_events", {"context_codes[]": [f"course_{cid}"],
            "start_date": (now - timedelta(days=180)).date().isoformat(),
            "end_date": (now + timedelta(days=365)).date().isoformat()})
        return [record(cid, "event", i, api.base) for i in items]
    await collect(cid, "event", calendar)

    async def todo():
        items = await api.all(f"{prefix}/todo")
        return [record(cid, "todo", {**i, "id": (i.get("assignment") or i.get("quiz") or {}).get("id") or i.get("html_url"),
                                     "title": (i.get("assignment") or i.get("quiz") or {}).get("name") or "To-do item"},
                       api.base) for i in items]
    await collect(cid, "todo", todo)
