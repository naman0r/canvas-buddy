import json
import os
import shutil
import tempfile
from ipaddress import ip_address
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


def data_home():
    explicit = os.environ.get("CANVAS_BUDDY_HOME") or os.environ.get("CANVAS_RAG_HOME")
    if explicit:
        return Path(explicit).expanduser()
    root = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    legacy = root / "canvas-rag"
    return legacy if (legacy / "config.json").exists() else root / "canvas-buddy"


def read_json(path):
    try:
        value = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, OSError):
        raise ValueError(f"Cannot read {path.name} in {path.parent}. Restore it from backup or use a new CANVAS_BUDDY_HOME.") from None


def private_json(path, value):
    """Replace atomically; the temporary file is private from the moment it is created."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".config-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@dataclass
class Config:
    home: Path = field(default_factory=data_home)
    url: str = ""
    token: str = field(default="", repr=False)
    courses: list[int] = field(default_factory=list)
    provider: str = "codex"
    model: str = ""
    ollama: str = "http://127.0.0.1:11434"
    embed_model: str = ""

    @classmethod
    def load(cls):
        # A .env is honoured only inside a source checkout, so launching from an unrelated project
        # directory cannot silently repoint the app. Installations use saved credentials.
        cwd = Path.cwd()
        checkout = (cwd / "pyproject.toml").exists() and (cwd / "canvas_rag" / "__init__.py").exists()
        env = {**(dotenv_values(cwd / ".env") if checkout else {}), **os.environ}
        c = cls()
        c.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        c.home.chmod(0o700)
        saved = read_json(c.home / "config.json")
        if not saved and not shutil.which("codex") and shutil.which("opencode"):
            c.provider = "opencode"
        for key in ("url", "courses", "provider", "model", "ollama", "embed_model"):
            if key in saved:
                setattr(c, key, saved[key])
        for key, name in {"url": "CANVAS_URL", "provider": "CANVAS_RAG_PROVIDER",
                          "model": "CANVAS_RAG_MODEL", "ollama": "OLLAMA_URL",
                          "embed_model": "CANVAS_RAG_EMBED_MODEL"}.items():
            if env.get(name):
                setattr(c, key, env[name])
        credentials = read_json(c.home / "credentials.json")
        c.token = env.get("CANVAS_PAT") or credentials.get("token") or ""
        if not all(isinstance(getattr(c, k), str) for k in ("url", "token", "provider", "model", "ollama", "embed_model")):
            raise ValueError("Invalid settings: URL, provider and model must be text.")
        if not isinstance(c.courses, list) or any(type(i) is not int or i <= 0 for i in c.courses):
            raise ValueError("Invalid saved course selection; expected positive course IDs.")
        if c.provider not in {"codex", "opencode", "ollama"}:
            raise ValueError("Invalid provider; choose codex, opencode or ollama.")
        c.url = c.url.strip().rstrip("/").removesuffix("/api/v1")
        if not env.get("CANVAS_PAT") and credentials.get("url") != c.url:
            c.token = ""  # A saved PAT must never be sent to a different Canvas site.
        return c

    def save(self):
        data = {k: getattr(self, k) for k in ("url", "courses", "provider", "model", "ollama", "embed_model")}
        private_json(self.home / "config.json", data)

    def save_token(self):
        private_json(self.home / "credentials.json", {"url": self.url, "token": self.token})

    def error(self, error):
        message = str(error) or type(error).__name__
        return message.replace(self.token, "[redacted]") if self.token else message

    def validate_canvas(self):
        u = urlsplit(self.url)
        if u.scheme != "https" or not u.hostname or u.username or u.query or u.fragment or u.path not in ("", "/"):
            raise ValueError("Enter your Canvas HTTPS site URL, without a course path.")
        if not self.token:
            raise ValueError("Enter a Canvas access token in setup, or set CANVAS_PAT.")

    def validate_ollama(self):
        u = urlsplit(self.ollama)
        try:
            local = u.hostname == "localhost" or ip_address(u.hostname or "").is_loopback
            valid_port = u.port is None or u.port > 0
        except ValueError:
            local = valid_port = False
        if not (local and valid_port and u.scheme in {"http", "https"}) or u.username or u.query or u.fragment or u.path not in ("", "/"):
            raise ValueError("Ollama must use a local address, such as http://127.0.0.1:11434. Check OLLAMA_URL.")
