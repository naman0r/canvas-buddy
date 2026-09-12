"""Diagnostics and an offline installation check; neither requires course credentials."""
import json
import shutil
import tempfile
from pathlib import Path

import httpx

from .canvas import record
from .config import Config
from .store import Store

GUIDANCE = {
    "codex": "Install/update Codex CLI, then run: codex login (https://developers.openai.com/codex/cli)",
    "opencode": "Install OpenCode, then run: opencode auth login (https://opencode.ai)",
    "ollama": "Install/start Ollama and choose an installed chat model in setup (https://ollama.com)",
}


async def doctor(config, db):
    print(json.dumps({"canvas_url": config.url, "canvas_pat_set": bool(config.token),
        "courses": config.courses, "provider": config.provider, "model": config.model or "provider default",
        "executables": {x: shutil.which(x) for x in GUIDANCE}, "data": str(config.home),
        "search": config.embed_model or "keyword (Ollama not required)"}, indent=2))
    print(GUIDANCE[config.provider])
    if not config.token or not config.courses:
        print("Start canvas-buddy and complete Courses setup with your own Canvas URL/token.")
    if config.embed_model or config.provider == "ollama":
        try:
            config.validate_ollama()
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                r = await client.get(config.ollama + "/api/tags")
                r.raise_for_status()
                models = [x["name"] for x in r.json()["models"]]
            print("Ollama available. Installed models: " + ", ".join(models))
            if config.embed_model and not any(m.removesuffix(":latest") == config.embed_model.removesuffix(":latest") for m in models):
                print(f"Embedding model missing. Run: ollama pull {config.embed_model}")
        except (httpx.HTTPError, ValueError, KeyError):
            print("Ollama unavailable. Start Ollama, or disable local embeddings in Courses setup.")
    print(db.status())


async def self_test():
    with tempfile.TemporaryDirectory(prefix="canvas-buddy-test-") as directory:
        c = Config(home=Path(directory), courses=[1], embed_model="")
        db = Store(c.home)
        try:
            item = record(1, "page", {"id": 1, "title": "Sample syllabus"}, "https://canvas.example",
                          "Attendance is required. Office hours are Tuesday.")
            db.replace(1, "page", [item])
            hits = await db.search("attendance", c)
            if len(hits) != 1 or hits[0]["title"] != "Sample syllabus":
                raise RuntimeError("Local search self-test failed")
            db.replace(1, "page", [])
            if await db.search("attendance", c):
                raise RuntimeError("Cache removal self-test failed")
        finally:
            db.close()
    print("Self-test passed: SQLite, keyword search, and cache updates (no network or credentials).")
