# Canvas Buddy

Ask questions about your Canvas classes from a small terminal app. Answers link back to the source. Browse syllabi, announcements, assignments, grades, pages and files without opening a dozen tabs.

MIT licensed. Local storage. Read-only Canvas access. Uses your existing Codex/OpenCode CLI login, or a local Ollama chat model.

**Current status:** the source branch is a personal-testing preview (0.3.0.dev0); Homebrew remains at 0.2.0. Broader account onboarding requires OAuth. See the [review](docs/REVIEW.md) and [Canvas authentication requirements](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth#manual-token-generation).

## Try the source preview without credentials

With Python 3.11+ and uv installed:

```sh
git clone https://github.com/naman0r/canvas-buddy.git
cd canvas-buddy
uv run canvas-buddy demo
```

The demo uses disposable fictional classes, never reads saved credentials, and makes no network or model calls. Replies show matching sample excerpts. Try **Upcoming**, **Grades**, and **Browse**; the browser has a filter and closes with Escape. In the regular app, a persistent timestamp/coverage summary shows how current and complete the cache is.

## Install the released app on macOS

With [Homebrew](https://brew.sh) installed:

```sh
brew install naman0r/tap/canvas-buddy
canvas-buddy
```

Prebuilt Homebrew packages support Apple Silicon on macOS 14+ and Intel on macOS 15+.

No Python setup, repository clone, `.env` file, or Ollama installation is required for keyword search. Homebrew installs the Python runtime and application dependencies.

For model answers, install and sign into one supported CLI before asking a question:

- [Codex CLI](https://developers.openai.com/codex/cli): `brew install --cask codex`, then `codex login`. Use a current version supporting `exec --ignore-user-config --ephemeral` (verified with 0.153.4).
- [OpenCode](https://opencode.ai): install using its instructions, then `opencode auth login`. Choose your provider/model in Canvas Buddy setup if needed. Availability and billing depend on that provider/account.
- [Ollama](https://ollama.com): install/start it and pull a chat model appropriate for your computer. Choose `ollama` and that exact model name in setup. No model is automatically downloaded.

Without a model CLI, you can still sync, browse, search, and inspect grades/deadlines. `canvas-buddy doctor` reports what is available and what needs setup.

## Personal testing setup

1. Enter your school's **Canvas HTTPS URL**.
2. Paste your **Canvas access token**. In Canvas: **Account → Settings → New Access Token**. Some schools disable personal tokens; those accounts cannot use this app without their institution enabling API access.
3. Choose Codex, OpenCode, or Ollama. The model field is optional for CLI providers; Ollama needs an installed model name.
4. Leave local embeddings unchecked for a lightweight start. Load courses, select classes with Space, then **Save & sync**.

Setup saves your selection and credentials on this computer. Relaunch `canvas-buddy` from any directory. Open **Courses** or run `canvas-buddy setup` to change the selection. Unselected courses are removed locally after saving; nothing changes in Canvas.

Try questions like:

- “What is the attendance policy for Investments?”
- “What assignments are due this week?”
- “When is the midterm? Cite the syllabus.”

Choose a class in the top dropdown when asking about “this class.” Your messages have a yellow accent; replies have a cyan accent. An animated status shows the current step and elapsed time, then returns to **Ready**. Cancel with Esc or the **Cancel** button.

**Sync is manual.** Click Sync or press Ctrl+R for fresh data. Answers use cached timestamps and may be incomplete; follow the source links for important policies and deadlines.

## Optional semantic search

Keyword search works out of the box. To also match similar meanings:

```sh
ollama pull nomic-embed-text
```

Enable **Use local Ollama embeddings** in Courses setup and save/sync. The embedding model is about 274 MB. It runs locally and stores vectors in SQLite. No vector database server is needed. If Ollama fails, search falls back to keywords; `/status` reports embedding availability.

## Commands

| Command | Purpose |
| --- | --- |
| `/setup` | Change Canvas connection, provider, embeddings, or courses |
| `/sync` or Ctrl+R | Refresh content and embed changed text |
| `/browse` or Ctrl+B | Read full cached documents |
| `/search attendance` | Local search without calling a chat model |
| `/upcoming` | Dated assignments/events in the next 30 days |
| `/overdue` | Past-due assignments not submitted/graded/excused |
| `/grades` | Canvas current/final grade values; null means not posted |
| `/status` | Counts, timestamps, partial imports and unavailable files |
| `/provider codex [model]` | Change the answer provider; also accepts opencode/ollama |
| `/clear` | Forget the in-memory conversation |
| Ctrl+Q | Quit |

CLI commands work too:

```sh
canvas-buddy --version
canvas-buddy doctor
canvas-buddy self-test                       # Offline installation check
canvas-buddy sync
canvas-buddy ask --course 123 "When is the exam?"
canvas-buddy search "attendance"
canvas-buddy upcoming
canvas-buddy grades
```

The legacy `canvas-rag` command remains available. `--provider` and `--model` override a single CLI invocation. Shell scripts can use `CANVAS_URL` / `CANVAS_PAT` and `canvas-buddy setup --courses 123,456`.

## Coverage and limits

Imported when your account can access it:

- Course details, HTML syllabus, instructor/TA names.
- Assignments with personalized deadlines, submission states, scores, rubric assessments and feedback.
- Canvas enrollment grades and assignment-group weighting/rules.
- Announcements, discussions and accessible threads.
- Pages, front page, modules and linked content, even when the Pages/Files listing is hidden.
- Classic quiz metadata and availability; course calendar events from 180 days ago through 365 days ahead. Assignment dates are imported independently.
- Files: text from PDF, DOCX, PPTX, XLSX and common text formats. Download limit: 25 MB per file. Original files are not retained. Spreadsheet extraction is raw text, not formula evaluation.
- Inbox conversations explicitly associated with selected courses, without marking them read.

`/status` distinguishes complete endpoint snapshots, partial linked-content discovery, and failures. Some file records contain only metadata and a link because extraction or access failed.

Locked/unpublished materials, external publisher/LTI tools, Gradescope, Panopto, remote websites, audio/video, OCR and image-only documents are outside the current scope. New Quizzes may expose assignment metadata without the external tool's full content. An empty assignment list does not mean there is no work due. Model answers can be wrong or miss evidence.

## Privacy

Canvas requests are **GET only**. The app cannot submit work, post, change grades, or message classmates. The token is excluded from model prompts, subprocess environments and normal configuration. It is stored separately in `credentials.json`, bound to the Canvas URL, with owner-only permissions. This is local file protection, **not separate encryption**; use an encrypted computer and private OS account.

New installations store data under `~/.local/share/canvas-buddy` (`$XDG_DATA_HOME/canvas-buddy` on systems that set it). Existing `canvas-rag` installations retain their old directory. Set `CANVAS_BUDDY_HOME` to use another directory or Canvas account; the cache is bound to the original account to prevent mixing data. Back up this private directory with your normal encrypted backups.

**Local storage does not mean local inference with Codex/OpenCode.** Those providers receive your question, selected excerpts, relevant grade/date context and short conversation history under your existing account. Their own retention rules apply. Use Ollama chat for local inference too. In the source preview, Ollama endpoints must be loopback addresses and proxy environment variables are ignored for those requests. The TUI keeps conversation history only in memory; external CLIs may keep their own logs.

API pagination must stay on your Canvas origin. File downloads do not forward the PAT. Codex runs ephemerally with user configuration, host skill discovery and action tools disabled; OpenCode uses a tool-denied agent with sharing disabled. These restrictions reduce exposure; they are not a substitute for your provider's privacy/security policies.

Environment variables override saved preferences: `CANVAS_URL`, `CANVAS_PAT`, `CANVAS_RAG_PROVIDER`, `CANVAS_RAG_MODEL`, `OLLAMA_URL`, and `CANVAS_RAG_EMBED_MODEL`. A `.env` in the current directory is also read for compatibility with development checkouts. To switch schools, use setup and provide the new token, or choose a new data directory.

## Updates and troubleshooting

```sh
brew update
brew upgrade naman0r/tap/canvas-buddy
canvas-buddy doctor
canvas-buddy self-test
```

- **Canvas 401:** the token expired or is invalid. Open Courses and enter a fresh token.
- **Canvas 403/404:** your account cannot access that endpoint, or the course hides it. Check `/status`; module links may recover some content.
- **Model missing/login failure:** follow `doctor` instructions and verify the provider's own CLI works. Restart after installing a CLI or changing your shell PATH.
- **No matching sources:** choose the correct course and sync. Optional embeddings improve semantic matches.
- **Another sync is running:** let it finish or cancel it in the other app window.

`brew uninstall canvas-buddy` removes the app but keeps private data. Remove the data directory separately to erase tokens and cached classes. No daemon, scheduler, or automatic model download is installed.

## Development

Python 3.11+ on macOS/Linux. Windows is not currently supported. The release formula uses Homebrew Python 3.13.

```sh
git clone https://github.com/naman0r/canvas-buddy.git
cd canvas-buddy
uv sync --frozen
uv run canvas-buddy
uv run pytest -q
uv run ruff check canvas_rag tests
uv build
```

Five runtime dependencies: Textual, HTTPX, Beautiful Soup, python-dotenv and pypdf. SQLite/FTS5, float-vector storage and similarity scoring use Python's standard library. Unchanged files and embeddings are reused; resource snapshots update atomically. Failed endpoints retain previous timestamps and cached data. Concurrent syncs are blocked.

Inspired by [canvas-mcp](https://github.com/vishalsachdev/canvas-mcp), implemented independently against the [Canvas REST API](https://developerdocs.instructure.com/services/canvas). See [RELEASING.md](RELEASING.md) for the release/tap workflow.

See [SECURITY.md](SECURITY.md) for security boundaries and reporting, and [the sharing plan](docs/SHARING.md) for researched communities and pitch drafts.
