import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


@dataclass
class Config:
    home: Path = field(default_factory=lambda: Path(os.environ.get(
        "CANVAS_RAG_HOME", "~/.local/share/canvas-rag")).expanduser())
    url: str = ""
    token: str = field(default="", repr=False)
    courses: list[int] = field(default_factory=list)
    provider: str = "codex"
    model: str = ""
    ollama: str = "http://127.0.0.1:11434"
    embed_model: str = "nomic-embed-text"

    @classmethod
    def load(cls):
        env = {**dotenv_values(Path.cwd() / ".env"), **os.environ}
        c = cls(home=Path(env.get("CANVAS_RAG_HOME", "~/.local/share/canvas-rag")).expanduser())
        c.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        c.home.chmod(0o700)
        saved = json.loads((c.home / "config.json").read_text()) if (c.home / "config.json").exists() else {}
        for key in ("url", "courses", "provider", "model", "ollama", "embed_model"):
            if key in saved:
                setattr(c, key, saved[key])
        for key, name in {"url": "CANVAS_URL", "provider": "CANVAS_RAG_PROVIDER",
                          "model": "CANVAS_RAG_MODEL", "ollama": "OLLAMA_URL",
                          "embed_model": "CANVAS_RAG_EMBED_MODEL"}.items():
            if env.get(name):
                setattr(c, key, env[name])
        c.token = env.get("CANVAS_PAT") or ""
        c.url = c.url.rstrip("/").removesuffix("/api/v1")
        return c

    def save(self):
        data = {k: getattr(self, k) for k in ("url", "courses", "provider", "model", "ollama", "embed_model")}
        path = self.home / "config.json"
        path.write_text(json.dumps(data, indent=2) + "\n")
        path.chmod(0o600)

    def validate_canvas(self):
        u = urlsplit(self.url)
        if u.scheme != "https" or not u.hostname or u.username or u.query or u.path not in ("", "/"):
            raise ValueError("Enter your Canvas HTTPS site URL, without a course path.")
        if not self.token:
            raise ValueError("Set CANVAS_PAT in your .env or environment, then restart.")
