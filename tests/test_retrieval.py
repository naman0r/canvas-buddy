import io
import zipfile
from array import array

import httpx
import pytest

from canvas_rag.canvas import extract, record
from canvas_rag.config import Config
from canvas_rag.store import Store


@pytest.fixture
def config(tmp_path):
    return Config(home=tmp_path, url="https://canvas.example", token="test-secret", courses=[1, 2],
                  ollama="http://127.0.0.1:1")


@pytest.fixture
def db(config):
    db = Store(config.home)
    yield db
    db.close()


def doc(cid=1, kind="page", id=1, title="Syllabus", body="Attendance is required.", **raw):
    return record(cid, kind, {"id": id, "title": title, **raw}, "https://canvas.example", body)


def pdf(text):
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def office(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def vector(*values):
    return array("f", values).tobytes()


def set_vector(db, doc_id, values, model):
    with db.conn:
        db.conn.execute("UPDATE chunks SET vector=?,model=? WHERE doc=?", (vector(*values), model, doc_id))


AsyncClient = httpx.AsyncClient


def mock_embed(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: AsyncClient(transport=transport, **kwargs))


def test_extract_pdf_text():
    text = extract(pdf("Attendance is required"), "syllabus.pdf")
    assert text.startswith("Page 1")
    assert "Attendance is required" in text


def test_extract_docx_pptx_xlsx():
    docx = office({
        "word/document.xml": "<w:document xmlns:w='w'><w:body><w:p><w:r><w:t>Zebra</w:t></w:r></w:p></w:body></w:document>",
        "word/settings.xml": "<w:settings xmlns:w='w'><w:zoom>Hidden</w:zoom></w:settings>",
    })
    pptx = office({
        "ppt/slides/slide1.xml": "<p:sld xmlns:p='p'><p:txBody><a:t xmlns:a='a'>Giraffe</a:t></p:txBody></p:sld>",
        "ppt/slideLayouts/slideLayout1.xml": "<p:sldLayout xmlns:p='p'><a:t xmlns:a='a'>Hidden</a:t></p:sldLayout>",
    })
    xlsx = office({
        "xl/sharedStrings.xml": "<sst xmlns='s'><si><t>Walrus</t></si></sst>",
        "xl/worksheets/sheet1.xml": "<worksheet xmlns='s'><sheetData><row><c><v>42</v></c></row></sheetData></worksheet>",
        "xl/styles.xml": "<styleSheet xmlns='s'><font>Hidden</font></styleSheet>",
    })
    for data, name, words in [(docx, "a.docx", ["Zebra"]), (pptx, "b.pptx", ["Giraffe"]),
                              (xlsx, "c.xlsx", ["Walrus", "42"])]:
        text = extract(data, name)
        assert all(w in text for w in words), (name, text)
        assert "Hidden" not in text


def test_extract_rejects_oversized_office_expansion():
    data = office({"word/document.xml": b"a" * 81_000_000})
    assert len(data) < 200_000
    with pytest.raises(ValueError, match="80 MB"):
        extract(data, "big.docx")


def test_extract_plain_text_and_html():
    html = extract(b"<p>Office hours <b>Tuesday</b></p><script>x()</script>", "notes.html")
    assert html.split() == ["Office", "hours", "Tuesday"]
    assert extract(b"Attendance is required\n", "notes.txt").strip() == "Attendance is required"
    assert "bad" in extract(b"\xff\xfe bad", "notes.txt")


async def test_hybrid_ranking_vector_hit_outranks_weak_keyword_hit(db, config, monkeypatch):
    config.embed_model = "nomic-embed-text"
    db.replace(1, "page", [
        doc(id=1, title="Both", body="Attendance policy for lectures"),
        doc(id=2, title="Keyword only", body="Attendance sheet is passed around"),
        doc(id=3, title="Vector only", body="Show up to every lecture or lose points"),
    ])
    set_vector(db, "1:page:1", [1, 0, 0], config.embed_model)
    set_vector(db, "1:page:3", [0.9, 0.1, 0], config.embed_model)
    mock_embed(monkeypatch, lambda request: httpx.Response(200, json={"embeddings": [[1, 0, 0]]}))
    titles = [h["title"] for h in await db.search("attendance", config)]
    assert titles[0] == "Both"
    assert set(titles) == {"Both", "Keyword only", "Vector only"}

    def refuse(request):
        raise httpx.ConnectError("connection refused")
    mock_embed(monkeypatch, refuse)
    titles = [h["title"] for h in await db.search("attendance", config)]
    assert set(titles) == {"Both", "Keyword only"}


async def test_vector_search_scoped_to_course(db, config, monkeypatch):
    config.embed_model = "nomic-embed-text"
    db.replace(1, "page", [doc(cid=1, id=1, title="Course one", body="Nothing keyword-matchable here")])
    db.replace(2, "page", [doc(cid=2, id=2, title="Course two", body="Nothing keyword-matchable here")])
    set_vector(db, "1:page:1", [0, 1], config.embed_model)
    set_vector(db, "2:page:2", [1, 0], config.embed_model)
    mock_embed(monkeypatch, lambda request: httpx.Response(200, json={"embeddings": [[1, 0]]}))
    assert [h["title"] for h in await db.search("attendance", config, course=1)] == ["Course one"]
    assert [h["title"] for h in await db.search("attendance", config, course=2)] == ["Course two"]
    assert [h["title"] for h in await db.search("attendance", config)] == ["Course two", "Course one"]


def seed_eval(db):
    db.replace(1, "course", [record(1, "course", {"id": 1, "name": "Biology 101"}, "https://canvas.example",
                                    "Biology 101. Introductory cell and molecular biology.")])
    db.replace(2, "course", [record(2, "course", {"id": 2, "name": "History 201"}, "https://canvas.example",
                                    "History 201. Europe from 1789 to 1914.")])
    db.replace(1, "page", [doc(cid=1, id=1, title="Biology syllabus", body=(
        "Attendance is required at every lecture; more than two unexcused absences lower your grade. "
        "Late work: a penalty of 10 percent per day applies, and nothing is accepted after one week. "
        "Office hours are Tuesday 2 to 4 pm in Room 210 of the Science Building."))])
    db.replace(2, "page", [doc(cid=2, id=2, title="History syllabus", body=(
        "Attendance is optional but participation counts for 15 percent. "
        "Essays turned in late lose 5 percent per day. "
        "Office hours are Thursday 10 am to noon in Humanities 305."))])
    db.replace(1, "assignment", [
        doc(cid=1, kind="assignment", id=11, title="Lab Report 1", due_at="2026-10-03T23:59:00Z",
            body="Write up the enzyme kinetics experiment. Include your raw data tables. Due Friday October 3."),
        doc(cid=1, kind="assignment", id=12, title="Problem Set 2", due_at="2026-10-10T23:59:00Z",
            body="Genetics problems covering Punnett squares and linkage maps."),
    ])
    db.replace(2, "assignment", [
        doc(cid=2, kind="assignment", id=21, title="Essay on the French Revolution", due_at="2026-10-15T23:59:00Z",
            body="Five pages on the causes of the French Revolution, Chicago citations."),
    ])
    db.replace(1, "announcement", [doc(cid=1, kind="announcement", id=31, title="Midterm room change",
        body="The midterm exam on October 20 moves to Hall B, room 140. Bring a calculator.")])
    db.replace(1, "discussion", [doc(cid=1, kind="discussion", id=41, title="Study group for the final",
        body="Anyone want to form a study group for the final exam? Library third floor.")])
    db.replace(1, "file", [doc(cid=1, kind="file", id=51, title="Lecture 3 slides.pdf",
        body="Lecture 3 slides.pdf\n\nPage 1\nPhotosynthesis: light reactions and the Calvin cycle.")])
    db.replace(1, "grade", [record(1, "grade", {"id": 61, "grades": {"current_score": 88.5, "current_grade": "B+"}},
                                   "https://canvas.example")])


EVAL_CASES = [
    ("attendance policy", "Biology syllabus", 1),
    ("when are office hours", "Biology syllabus", 1),
    ("late penalty", "Biology syllabus", 1),
    ("where is the midterm", "Midterm room change", 1),
    ("what is due for the lab report", "Lab Report 1", 1),
    ("when is it due", "Lab Report 1", 1),
    ("attendance", "History syllabus", 2),
    ("office hours", "History syllabus", 2),
    ("punnett squares", "Problem Set 2", None),
    ("photosynthesis", "Lecture 3 slides.pdf", None),
    ("what is my current grade", "grade", 1),
    ("exam room change", "Midterm room change", None),
    ("french revolution essay", "Essay on the French Revolution", None),
    ("study group", "Study group for the final", 1),
    ("when are office hours what room", "Biology syllabus", 1),
]


@pytest.mark.parametrize("question,expected,course", EVAL_CASES)
async def test_retrieval_eval_set(db, config, question, expected, course):
    config.embed_model = ""
    seed_eval(db)
    titles = [h["title"] for h in await db.search(question, config, course)]
    assert expected in titles[:3], titles


@pytest.mark.parametrize("question", ["field trip permission slip", "parking permit"])
async def test_retrieval_eval_absent_topics(db, config, question):
    config.embed_model = ""
    seed_eval(db)
    assert await db.search(question, config) == []
