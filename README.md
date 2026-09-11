# Canvas Buddy

A small, local terminal companion for Canvas LMS. Choose your classes, sync once, and ask questions with links back to the source. MIT licensed. No hosted backend, vector service, agent framework, or API key for chat when using an authenticated subscription CLI.

## Run

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and an authenticated [Codex CLI](https://developers.openai.com/codex/noninteractive) or [OpenCode](https://opencode.ai/docs/cli/).

```sh
cp .env.example .env
# Set CANVAS_URL and CANVAS_PAT in .env
uv sync
ollama pull nomic-embed-text
uv run canvas-rag
```

The course picker reads your Canvas token from `.env` in the current directory, or the environment. Click **Load courses**, toggle the classes you want with Space, then **Save & sync**. Open **Courses** or `/setup` any time to replace the selection. Deselected courses are removed from the cache on the next successful authenticated sync.

On Naman's homelab, setup is already saved. Run:

```sh
cd ~/developer/canvas-rag && uv run canvas-rag
```

Your messages have a yellow accent; Canvas Buddy replies have a cyan accent. While working, an animated status shows the current step and elapsed seconds. It distinguishes local retrieval from waiting for your model provider, notes when Ollama may be loading, and returns to **Ready** when finished. Use **Cancel** or Esc to stop a pending request.

Ask “What did the professor say about attendance?”, “What's due this week?”, or “When is the investments exam?” Use the course selector to focus results. The cache works offline; model answers still need the selected provider. Sync is manual: press **Sync**, Ctrl+R, or type `/sync` when you want fresh data.

## Commands

| TUI command | Purpose |
| --- | --- |
| `/setup` | Connect and change selected courses |
| `/sync` | Refresh content and embed changed text |
| `/browse` or Ctrl+B | Read full cached documents by course and type |
| `/search attendance` | Hybrid local search without a chat model call |
| `/upcoming` | All dated assignments/events in the next 30 days |
| `/overdue` | Past-due assignments without a submitted/graded/excused state |
| `/grades` | Canvas-reported current/final grades; null means not posted |
| `/status` | Counts, per-section freshness, failures and unextracted files |
| `/provider codex [model]` | Use the existing Codex login; default provider |
| `/provider opencode [provider/model]` | Use the existing OpenCode login |
| `/provider ollama [model]` | Local answers; defaults to `qwen3.6:35b` |
| `/clear` | Forget the in-memory conversation |
| Esc / Ctrl+Q | Cancel work / quit |

CLI equivalents work without the TUI:

```sh
uv run canvas-rag courses
uv run canvas-rag setup --url https://school.instructure.com --courses 123,456
uv run canvas-rag sync
uv run canvas-rag ask --course 123 "What is the attendance policy?"
uv run canvas-rag search "exam"
uv run canvas-rag upcoming
uv run canvas-rag grades
uv run canvas-rag status
uv run canvas-rag doctor
```

`--provider` and `--model` override a CLI invocation. Set `CANVAS_RAG_PROVIDER` / `CANVAS_RAG_MODEL` in `.env` for an environment default. The TUI provider command saves a preference; environment values override that preference at next launch. Codex requires a recent CLI supporting `exec --ignore-user-config --ephemeral`.

## Coverage

- Course details, HTML syllabus, instructor/TA names.
- Assignments, personalized deadlines, submission state, scores, rubric assessments and submission comments.
- Canvas enrollment grades and assignment-group weighting/rules.
- Announcements, discussion topics and their accessible threads.
- Pages, front page, module structure and module items.
- Classic quiz metadata/descriptions and availability; no taking quizzes or revealing restricted questions.
- Course calendar events: 180 days back through 365 days forward; all assignment dates are imported independently.
- Course files, including files linked from modules/content when the Files tab is hidden. Text extraction for PDF, DOCX, PPTX, XLSX and common text formats. Files are capped at 25 MB; originals are not retained.
- Inbox conversations explicitly associated with selected courses, fetched without marking them read.

`/status` distinguishes complete endpoint snapshots, partial linked-content discovery, and failed endpoints. Partial listings cannot establish that every item was discovered. Files with unavailable text retain a visible source record.

Canvas permissions still apply. Locked/unpublished materials, external LTI tools (publisher homework, Gradescope, Panopto, etc.), embedded remote websites, media, image-only documents and OCR aren't imported. New Quizzes may expose assignment metadata without their full external-tool content. XLSX extraction is raw cell/shared-string text, not a rendered spreadsheet or formula evaluation. Discussion/inbox visibility is what the API grants your account. Some courses keep work entirely outside Canvas, so an empty assignment list does not establish that nothing is due.

## How it works

`Canvas GET → SQLite documents/chunks → FTS5 + local Ollama vectors → selected excerpts → model CLI`

Six direct runtime dependencies: Textual, HTTPX, Beautiful Soup, python-dotenv, NumPy, pypdf. SQLite/FTS5 comes with Python. [nomic-embed-text](https://ollama.com/library/nomic-embed-text) is approximately 274 MB. Vectors live in SQLite; NumPy cosine similarity and reciprocal rank fusion combine semantic and keyword matches. If Ollama is unavailable, keyword search remains usable and the status reports the missing embeddings.

Changed text is rechunked; unchanged files and embeddings are reused. Each resource snapshot is committed atomically, including deletions. Failed endpoints retain previous snapshots and timestamps; successful sections remain cached if a sync is cancelled. The cache is bound to the Canvas host/account to prevent mixing identities. To change accounts, set a new `CANVAS_RAG_HOME`.

One model invocation per question, at most eight retrieved excerpts, a bounded structured snapshot for dates/grades, and three short recent conversation turns. Full course archives are not uploaded. Retrieved content is treated as evidence; prompts require source links and acknowledge missing coverage. Like any model, answers can still be wrong: follow the Canvas citations for important dates/policies.

## Privacy and storage

The app makes **GET requests only** to Canvas. It never submits work, posts, changes grades, or sends messages. The PAT stays out of model prompts, subprocess environments and saved configuration. API pagination must remain on your Canvas origin; file downloads follow HTTPS links without forwarding the PAT.

Data and embeddings are stored under `~/.local/share/canvas-rag` (override with `CANVAS_RAG_HOME`). The directory is private to your OS account; the SQLite cache is not separately encrypted. Back up that directory with your normal encrypted backups. `.env`, databases and virtual environments are ignored by Git. The TUI conversation is held in memory only.

**Local storage is not local inference when using Codex/OpenCode.** Those CLIs send the question, selected course text, and relevant grade/date context to their model service under your existing account. They may retain their own logs/history. Codex runs ephemerally with user configuration, host skill discovery and action tools disabled; OpenCode uses a tool-denied agent and disables sharing. The app does not copy CLI credentials. For local inference too, select `/provider ollama qwen3.6:35b` (or another installed chat model).

No server or scheduled job is installed. Stop with Ctrl+Q. To uninstall, remove this checkout and its virtual environment; remove the data directory separately if you want to erase cached course data. `ollama rm nomic-embed-text` removes the embedding model.

## Development

```sh
uv sync
uv run pytest -q
uv run ruff check canvas_rag tests
```

Tests cover credential boundaries, pagination/retries, cache updates/deletion/rollback, identity isolation, retrieval scoping, deadlines, CLI adapters, and headless TUI interaction. Live Canvas/API permissions and provider subscriptions vary by account.

Inspired by [canvas-mcp](https://github.com/vishalsachdev/canvas-mcp). This is an independent, read-only implementation using the [Canvas REST API](https://developerdocs.instructure.com/services/canvas), with a local index and a terminal UI rather than a large MCP tool catalog. See [PLAN.md](PLAN.md) for the implementation plan.
