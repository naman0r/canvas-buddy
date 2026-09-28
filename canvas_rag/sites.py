"""Course websites the student added: fetched at sync next to Canvas and cached as that course's documents.

Only pages under the address the student chose are read: the page itself, pages or PDFs it links to
under the same site and path, and shared Google Docs/Slides/Sheets it links to (as their text export).
Requests carry no Canvas credentials or cookies. The model never fetches anything; it reads these pages
from the cache like any Canvas document.
"""
import asyncio
import csv
import hashlib
import io
import re
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .canvas import extract, plain, record

PAGE_LIMIT = 40
BYTES_LIMIT = 10_000_000


def scope(url):
    """The site and folder a course website lives in: links outside it are someone else's pages."""
    parts = urlsplit(url)
    folder = parts.path if parts.path.endswith("/") else parts.path.rsplit("/", 1)[0] + "/"
    return parts.scheme, parts.netloc, folder


def within(url, root):
    scheme, netloc, folder = scope(root)
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and parts.netloc == netloc and parts.path.startswith(folder) \
        and not parts.username


def google_export(url):
    """The plain-text export of a shared Google Doc, Slides deck or Sheet, where professors often keep
    the schedule or syllabus a course page links to. None for anything else."""
    m = re.match(r"https://docs\.google\.com/(document|presentation|spreadsheets)/d/([\w-]+)", url)
    if not m:
        return None
    kind, key = m.groups()
    return {"document": f"https://docs.google.com/document/d/{key}/export?format=txt",
            "presentation": f"https://docs.google.com/presentation/d/{key}/export/txt",
            "spreadsheets": f"https://docs.google.com/spreadsheets/d/{key}/export?format=csv"}[kind]


def sheet_text(data):
    """A sheet as lines a reader can follow without counting commas: after the header row, each cell is
    labelled with its column ("10 - 11 AM · Monday: Erika; Wednesday: Seamus")."""
    rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(data))]
    rows = [r for r in rows if any(r)]
    width = max((sum(map(bool, r)) for r in rows), default=0)
    header = next((i for i, r in enumerate(rows) if sum(map(bool, r)) >= max(2, width // 2)), None)
    out = []
    for i, row in enumerate(rows):
        if header is None or i <= header:
            out.append(" | ".join(c for c in row if c))
            continue
        names = rows[header]
        cells = [f"{names[j] if j < len(names) and names[j] else f'column {j + 1}'}: {c}"
                 for j, c in enumerate(row[1:], 1) if c]
        out.append(f"{row[0] or '—'} · " + "; ".join(cells) if cells else row[0])
    return "\n".join(out)


async def get(client, url, root, export):
    """Body, content type and encoding of one page. Redirects are followed by hand so that a page can
    never lead the fetcher off the student's site; a Google export may only hop to Google's own hosts."""
    for _ in range(6):
        async with client.stream("GET", url) as r:
            if r.is_redirect:
                url = urljoin(url, r.headers.get("location", ""))
                parts = urlsplit(url)
                host = parts.hostname or ""
                if export:
                    allowed = parts.scheme == "https" and (host == "docs.google.com"
                                                           or host.endswith(".googleusercontent.com"))
                else:
                    allowed = within(url, root)
                if not allowed:
                    raise ValueError("redirected outside the site")
                continue
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            data = bytearray()
            async for part in r.aiter_bytes():
                data.extend(part)
                if len(data) > BYTES_LIMIT:
                    raise ValueError("page exceeds 10 MB")
            return data, r.headers.get("content-type", "").split(";")[0].strip(), r.encoding
    raise ValueError("too many redirects")


async def fetch_site(root, client):
    """(records-ready pages, warnings) for one website: the root page, in-scope pages it links to, and
    Google Docs/Slides/Sheets it links to."""
    pages, warnings, queue, seen = [], [], [(root, None)], {root}
    while queue and len(pages) < PAGE_LIMIT:
        url, hint = queue.pop(0)
        export = google_export(url)
        try:
            data, kind, encoding = await get(client, export or url, root, bool(export))
        except (httpx.HTTPError, ValueError) as e:
            warnings.append(f"{url}: {e}" + (" (not shared publicly?)" if export and "HTTP 40" in str(e) else ""))
            continue
        if export and kind == "text/html":
            warnings.append(f"{url}: not shared publicly")  # Google answers private files with a sign-in page.
            continue
        if export:
            text = bytes(data).decode("utf-8-sig", errors="replace")
            if kind == "text/csv":
                text = sheet_text(text)
            title = hint or url
        elif kind == "application/pdf" or url.lower().endswith(".pdf"):
            text = await asyncio.to_thread(extract, bytes(data), "page.pdf")
            title = urlsplit(url).path.rsplit("/", 1)[-1] or url
        elif kind in {"text/html", "application/xhtml+xml", "text/plain", ""}:
            html = bytes(data).decode(encoding or "utf-8", errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            title = " ".join((soup.title.string or "").split()) if soup.title and soup.title.string else url
            for tag in soup(["nav", "header", "footer"]):
                tag.decompose()  # Site chrome repeats on every page and drowns out the content.
            text = plain(str(soup))
            # Only the page the student chose is followed; its links are the site's table of contents.
            if url == root:
                for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
                    link = urljoin(url, a["href"]).split("#")[0]
                    wanted = google_export(link) or (within(link, root) and not re.search(
                        r"\.(png|jpe?g|gif|svg|zip|ipynb|mp4|mov|pptx?|docx?|xlsx?)$", link, re.I))
                    if link not in seen and wanted:
                        seen.add(link)
                        queue.append((link, " ".join(a.get_text(" ").split()) or None))
        else:
            continue
        if text.strip():
            pages.append((url, title, text))
    if queue:
        warnings.append(f"stopped after {PAGE_LIMIT} pages")
    return pages, warnings


async def sync_sites(db, progress=lambda s: None, courses=None, transport=None):
    """Fetch every course's websites into the cache, one course at a time, as the 'site' kind."""
    context = db.context()
    # trust_env=False keeps proxies and netrc credentials out; get() follows redirects itself.
    async with httpx.AsyncClient(timeout=20, trust_env=False, transport=transport,
                                 headers={"User-Agent": "canvas-buddy (course website reader)"}) as client:
        for course, entry in context.items():
            if courses is not None and course not in courses:
                continue
            if not entry["sites"] and not db.documents(course, "site"):
                continue
            progress(f"{course} · course websites")
            records, warnings = [], []
            for root in entry["sites"]:
                pages, problems = await fetch_site(root, client)
                warnings += problems
                for url, title, text in pages:
                    # The digest makes a changed page a logged change; the page's own raw data never changes.
                    records.append(record(course, "site", {"id": url, "title": title, "html_url": url,
                                                           "digest": hashlib.sha256(text.encode()).hexdigest()},
                                          "", text))
            if entry["sites"] and not records:
                db.coverage(course, "site", "error", "; ".join(warnings)[:600] or "No readable pages")
                continue
            db.replace(course, "site", list({r["id"]: r for r in records}.values()))
            db.coverage(course, "site", "partial" if warnings else "ok",
                        f"{len(records)} pages" + ("; " + "; ".join(warnings[:5]) if warnings else ""))
