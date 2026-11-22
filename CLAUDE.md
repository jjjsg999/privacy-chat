# Claude Code Handoff — Privacy LLM

This file gives Claude Code the context needed to continue development in this repository. Read it before making changes.

## Repository status

- GitHub: `https://github.com/jjjsg999/privacy-chat`
- Branch: `main`
- Starting point: commit `ed0f381` (`Initial Privacy LLM codebase`); pushed to `origin/main`.
- Work since that commit (streaming, uploads, Models page, chat actions, themes, Artifacts) is recorded in `git log`. This file is tracked from that later commit onward.
- `git` may not be on `PATH` in the Claude desktop shell. A bundled copy exists under `%USERPROFILE%\.cache\codex-runtimes\...\dependencies\native\git\cmd\git.exe`.
- Local runtime files are intentionally excluded from Git: `.env`, `config.json`, `sensitive_terms.txt`, and `data/`. Never inspect, print, copy, or commit their secret or personal contents.
- Historical reports, screenshots and retired evaluation artifacts remain in the local checkout and are not part of the initial code commit. Do not bulk-add them.

## Product and implementation

Privacy LLM is a local-first personal assistant using Python, FastAPI, plain HTML/CSS/JavaScript, JSON/text persistence, OpenRouter, Presidio/spaCy, and optional Telegram polling. Web and Telegram share the engine and privacy processing.

Main modules:

- `app.py`: FastAPI lifecycle, web API, static pages, settings, and shared chat lock.
- `engine.py`: shared chat, OpenRouter calls, meeting analysis/Q&A, privacy-composed requests, and titles.
- `privacy_engine.py`: global sensitive terms, Presidio/spaCy detection, semantic reversible tokens, and rehydration.
- `storage.py`: file-based settings/session storage and public session views.
- `telegram_transport.py`: Telegram authorization, commands, polling, meeting transcript buffering.
- `adviser.py`, `adviser_api.py`, `persona_store.py`: Adviser Chat and Persona Library.
- `monthly_budget.py`, `dev_budget.py`: spending ceilings and request reservation controls.
- `documents.py`: local text extraction for uploads (PDF text layer, DOCX, text/CSV, and OCR for scans and images via rapidocr: English and Traditional Chinese; HEIC via pillow-heif). All limits live in one block; nothing is stored.
- `prompts/meeting_assistant.md`, `prompts/ask.md`, `prompts/artifacts.md`: runtime-loaded meeting intelligence, Q&A and artifact-format prompts.
- `static/`: responsive browser UI. `chat.js` (chat, streaming, edit/branch/copy, pin/archive, shortcuts), `artifacts.js` (artifact parser and side pane), `models.html/js` (price table, per-model spend), `theme.js`, `sidebar-resize.js`. `static/vendor/` contains locally bundled libraries (marked, DOMPurify, mermaid) with sources in its README.
- `tests/`: targeted Python and browser checks. Browser `.cjs` checks need Node, which may not be installed.

Read `PROJECT_HANDOFF_CURRENT.md` for the architecture, API, storage, privacy boundaries, UI and operational details as of 2026-10-27. It predates the features listed under "Added after the handoff" below. Read `AGENTS.md` for the original project contract. Where they differ, current source and this file describe actual behavior.

## Current feature boundaries

- Web chat, settings, privacy processing, OpenRouter model selection, Telegram chat, meeting transcript capture/analysis, full-transcript meeting Q&A, cross-channel sessions, Adviser Chat, Persona Library, and monthly spending controls are implemented.
- Telegram meeting capture batches messages and analyzes after five seconds of inactivity. It does not persist empty captures.
- Memory is intentionally retired. The Memory page is a placeholder and mutation APIs return not implemented. Do not restore or replace Memory unless the user explicitly requests it.
- Gmail IMAP/SMTP ingestion from the original contract is not implemented. Treat it as future work only when the user asks for it.
- There is no database, vector search, Docker, or multi-service deployment.

### Added after the handoff (web chat unless stated)

- Streaming replies with Stop (partial replies are saved and marked stopped). `StreamRehydrator` restores tokens split across chunks. Telegram still uses the non-streaming path.
- File upload (PDF, DOCX, TXT/MD/CSV, images) with local OCR, paste, drag-and-drop and HEIC. Only extracted text is sent to the model, masked like typed text; images are never sent as pixels (pixels cannot be masked). Optional preview-and-edit of OCR text is a Settings checkbox. Telegram does not read files or photos yet.
- Local titles for web chats right after the first reply (no model call); Telegram keeps the third-message AI title.
- Optional separate meeting model for meeting analysis and meeting questions on web and Telegram; per-channel custom instructions.
- Models page (`/models`, opened from Settings) with price filters and per-model spend. The ledger now records the model on each request; older entries show as untracked.
- Rename, search, Markdown export, copy buttons, edit-and-resend, branch into a new conversation, pin/archive, light/dark theme, keyboard shortcuts, resizable sidebar. Edit and branch apply to normal chats only.
- Artifacts: only when the user asks. The model wraps standalone content in `<artifact type=… title=… id=…>` (html, svg, markdown, mermaid, code). `artifacts.js` finds these blocks and shows them in a side pane (live source while streaming, rendered preview when saved, versions by repeating an id). HTML/SVG run in an iframe served from `/artifact-frame`, which has its own CSP (no network, inline scripts only) and a sandbox without `allow-same-origin`. Mermaid's library is inlined into that frame. Only the streamed web chat is told about artifacts.

Known limits: replies stop at 6,000 output tokens; an artifact can still navigate its own frame to an external address (not blockable by CSP), so treat untrusted uploads with care; no encryption at rest; no web search, tool calling, skills or Memory.

## Local development

Use Python 3.11 or 3.12. From PowerShell at the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m spacy download en_core_web_sm
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, or use `Start Privacy LLM.bat` on Windows. Configure the OpenRouter key in Settings or the local ignored `config.json`. Configure Telegram through local ignored `.env`; no token is needed for Web-only operation. The service binds to loopback and has no login; keep it local.

Targeted checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Do not run paid model calls as part of routine verification. Tests should mock provider and Telegram calls. Report clearly when checks were not run.

## Development rules

- Keep changes small and understandable. Prefer Python, FastAPI, plain browser JavaScript, and JSON/text files.
- Do not add React, databases, Redis, vector databases, Docker/Kubernetes, queues, microservices, LangChain, LiteLLM, or unnecessary abstraction.
- Web and Telegram are transports; privacy and LLM logic belong in the shared engine, not duplicated in adapters.
- Before any model request: apply global sensitive terms, then PII detection; replace detected values with semantic reversible tokens; call the model with sanitized content; rehydrate locally using the session token map. Keep token maps isolated per session.
- Keep sessions transport-independent and file-backed. Do not persist blank conversations or blank meetings.
- Treat meeting transcripts as noisy evidence. Preserve the executive-intelligence focus and use the full stored transcript for meeting Q&A. Do not invent facts.
- Load meeting prompts from disk at runtime. Keep titles stable once generated.
- Never commit `.env`, `config.json`, `sensitive_terms.txt`, `data/`, personal transcripts, API credentials, or temporary run artifacts. Review `git status` and staged filenames before every commit.
- Do not rewrite Git history or force-push without an explicit request. Push only when asked.
- Add small offline tests for new backend behavior and mock the provider; do not make paid calls in tests. Real-model checks happen only when the user asks, with a cheap or free model, and test conversations created in the real data folder are deleted afterwards.
- Do not weaken the `/artifact-frame` policy, add `allow-same-origin` to the artifact iframe, or send images to a model without an explicit, scoped decision.

## Suggested first steps for Claude Code

1. Read this file, `AGENTS.md`, `README.md`, and the relevant sections of `PROJECT_HANDOFF_CURRENT.md`.
2. Inspect `git status --short --branch` and recent commits before editing.
3. Ask what feature or change the user wants next; this handoff does not authorize a specific new feature.
