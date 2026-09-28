import json
import sqlite3
import subprocess
import sys

import httpx
import pytest

from canvas_rag.answer import generate
from canvas_rag.canvas import record
from canvas_rag.config import Config
from canvas_rag.store import Store
from canvas_rag.tools import PART_CHARS, Lookup, ReadOnlyStore, split

BASE = "https://canvas.example"


@pytest.fixture
def config(tmp_path):
    return Config(home=tmp_path, url=BASE, token="test-secret", courses=[1, 2], ollama="http://127.0.0.1:1")


@pytest.fixture
def db(config):
    db = Store(config.home)
    db.replace(1, "course", [record(1, "course", {"id": 1, "name": "Biology 101"}, BASE, "")])
    db.replace(2, "course", [record(2, "course", {"id": 2, "name": "History 201"}, BASE, "")])
    db.replace(1, "file", [record(1, "file", {"id": 5, "display_name": "Lecture 3.pdf"}, BASE,
                                  "Page 1\nPhotosynthesis happens in chloroplasts.")])
    db.replace(2, "page", [record(2, "page", {"id": 6, "title": "Essay rubric"}, BASE, "Thesis counts double.")])
    yield db
    db.close()


def rpc(config, *messages, course=0):
    out = subprocess.run([sys.executable, "-m", "canvas_rag.tools", str(config.home), str(course), "", config.ollama],
                         input="".join(json.dumps(m) + "\n" for m in messages), capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return [json.loads(line) for line in out.stdout.splitlines()]


def test_mcp_server_lists_read_only_tools_and_answers_calls(config, db):
    replies = rpc(config,
                  {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                  {"jsonrpc": "2.0", "method": "notifications/initialized"},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                  {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "search", "arguments": {"query": "photosynthesis"}}},
                  {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "read_document", "arguments": {"id": "1:file:5"}}},
                  {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "rm", "arguments": {}}},
                  {"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    tools = replies[1]["result"]["tools"]
    assert {t["name"] for t in tools} >= {"search", "read_document", "deadlines"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    assert "[1:file:5]" in replies[2]["result"]["content"][0]["text"]
    text = replies[3]["result"]["content"][0]["text"]
    assert text.startswith("Lecture 3.pdf (file, Biology 101)") and "chloroplasts" in text
    assert replies[4]["result"]["isError"] and replies[5]["error"]["code"] == -32601


def test_course_filter_pins_every_tool(config, db):
    replies = rpc(config, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "read_document", "arguments": {"id": "2:page:6"}}},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "search", "arguments": {"query": "thesis", "course": 2}}},
                  {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "list_courses", "arguments": {}}}, course=1)
    texts = [r["result"]["content"][0]["text"] for r in replies]
    assert texts[0].startswith("No cached document") and texts[1] == "No matching cached text."
    assert "Biology 101" in texts[2] and "History 201" not in texts[2]


def test_tool_connection_cannot_write(config, db):
    ro = ReadOnlyStore(config.home)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        ro.conn.execute("DELETE FROM documents")
    assert db.get("1:file:5")


async def test_long_documents_come_in_parts(config, db):
    body = "\n".join(f"Page {n}\n" + "word " * 400 for n in range(1, 13))
    db.replace(1, "file", [record(1, "file", {"id": 7, "display_name": "Long.pdf"}, BASE, body)])
    parts = split(body)
    assert len(parts) > 1 and all(len(p) <= PART_CHARS for p in parts) and "".join(parts) == body
    assert all(p.startswith("Page ") for p in parts)
    assert [len(p) for p in split("x" * (PART_CHARS * 2 + 5))] == [PART_CHARS, PART_CHARS, 5]
    lookup = Lookup(db, config)
    last = await lookup.call("read_document", {"id": "1:file:7", "part": 99})
    assert f"part {len(parts)} of {len(parts)}" in last


async def test_codex_gets_canvas_server_and_preamble_is_dropped(config, db, monkeypatch, tmp_path):
    executable = tmp_path / "codex"
    executable.write_text('''#!/usr/bin/env python3
import json,sys
args = sys.argv
command = next(a for a in args if a.startswith("mcp_servers.canvas.command="))
server = json.loads(next(a for a in args if a.startswith("mcp_servers.canvas.args=")).split("=", 1)[1])
assert server[:2] == ["-m", "canvas_rag.tools"] and server[3] == "1"
sys.stdin.read()
for event in [{"type": "item.completed", "item": {"type": "agent_message", "text": "Let me check."}},
              {"type": "item.started", "item": {"type": "mcp_tool_call", "tool": "read_document",
                                                 "arguments": {"id": "1:file:5"}}},
              {"type": "item.completed", "item": {"type": "agent_message", "text": "In chloroplasts."}}]:
    print(json.dumps(event), flush=True)
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    calls, texts = [], []
    reply = await generate(config, "q", texts.append, lookup=Lookup(db, config, 1),
                           on_tool=lambda name, args: calls.append((name, args)))
    assert reply == "In chloroplasts." and calls == [("read_document", {"id": "1:file:5"})]
    assert texts == ["Let me check.", "In chloroplasts."]


async def test_opencode_may_use_only_canvas_tools(config, db, monkeypatch, tmp_path):
    config.provider = "opencode"
    executable = tmp_path / "opencode"
    executable.write_text('''#!/usr/bin/env python3
import json,os,sys
c = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
agent = c["agent"]["canvas"]
assert c["permission"] == agent["permission"] == {"*": "deny", "canvas_*": "allow"}
assert agent["tools"] == {"*": False, "canvas_*": True}
assert c["mcp"]["canvas"]["command"][1:3] == ["-m", "canvas_rag.tools"]
sys.stdin.read()
print(json.dumps({"type": "tool_use", "part": {"tool": "canvas_search", "state": {"input": {"query": "x"}}}}))
print(json.dumps({"type": "text", "part": {"text": "done"}}))
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":/usr/bin:/bin")
    calls = []
    assert await generate(config, "q", lookup=Lookup(db, config),
                          on_tool=lambda name, args: calls.append(name)) == "done"
    assert calls == ["search"]


async def test_ollama_runs_tool_calls_in_process(config, db, monkeypatch):
    config.provider, config.model = "ollama", "m"
    bodies = []
    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            call = {"function": {"name": "read_document", "arguments": {"id": "1:file:5"}}}
            return httpx.Response(200, content=json.dumps({"message": {"content": "", "tool_calls": [call]}}).encode())
        return httpx.Response(200, content=json.dumps({"message": {"content": "Chloroplasts."}, "done": True}).encode())
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    assert await generate(config, "q", lookup=Lookup(db, config)) == "Chloroplasts."
    assert bodies[0]["tools"][0]["function"]["name"] == "search"
    tool_message = bodies[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "chloroplasts" in tool_message["content"]


async def test_ollama_model_without_tools_still_answers(config, db, monkeypatch):
    config.provider, config.model = "ollama", "m"
    def respond(request):
        if "tools" in json.loads(request.content):
            return httpx.Response(400, json={"error": "registry.ollama.ai/library/m does not support tools"})
        return httpx.Response(200, content=json.dumps({"message": {"content": "Plain."}, "done": True}).encode())
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    assert await generate(config, "q", lookup=Lookup(db, config)) == "Plain."


async def test_answer_reports_documents_the_model_read(config, db, monkeypatch):
    from canvas_rag import answer as module
    progress = []
    async def fake(c, prompt, on_text, lookup, on_tool):
        assert "[redacted]" not in prompt and "read_document" in prompt
        on_tool("read_document", {"id": "1:file:5"})
        return "ok"
    monkeypatch.setattr(module, "generate", fake)
    reply, sources = await module.answer(config, db, "essay rubric", progress=progress.append)
    assert reply == "ok" and "Reading Lecture 3.pdf…" in progress
    assert "1:file:5" in [s["id"] for s in sources]
