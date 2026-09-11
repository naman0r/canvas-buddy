# Canvas RAG implementation plan

1. Read-only Canvas client: paginated/retried requests, course selection, syllabus, assignments and personal submissions/feedback, grades and weighting, announcements, pages, modules, discussions, quizzes, calendar, files and personal inbox.
2. Private local SQLite cache: original records, searchable chunks, FTS5 + Ollama vectors, incremental embedding reuse, atomic per-resource sync, visible coverage/failures, removal of deselected courses.
3. Bounded retrieval with source citations, course scoping, structured deadline/grade context and short conversation history. One model call per question through existing authenticated Codex/OpenCode CLIs; optional Ollama chat.
4. Textual TUI: repeatable setup/course picker, chat, course filter, browse/read sources, upcoming work, grades, sync, status, cancellation. CLI equivalents for scripts and diagnostics.
5. Tests: Canvas pagination/auth boundaries/failure handling, index updates and retrieval, provider adapters, headless TUI. Verify against the actual Canvas account, local embeddings, and authenticated model CLI when connection details are available.
6. Document installation, usage, limitations and validation. Publish the source to `naman0r/canvas-buddy`.

Keep the implementation direct: no orchestration framework, separate vector service, containers, or background scheduler. Cache and embeddings remain local; CLI providers send questions and selected excerpts to the selected model service. Canvas is read-only.
