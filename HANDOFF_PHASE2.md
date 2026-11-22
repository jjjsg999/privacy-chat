# Privacy LLM: Phase 2 Handoff

**Written 2026-11-22.** Use this to start a new chat that builds the next phase. It is the current source of truth for state, architecture, rationale, design choices and the roadmap. `CLAUDE.md` holds the standing rules; `PROJECT_HANDOFF_CURRENT.md` (2026-10-27) is older background and predates most of what is described here. Where documents disagree, the source code wins, then this file.

Repository: `C:\Users\c_n_h\Documents\Codex\Privacy LLM`. Remote: `https://github.com/jjjsg999/privacy-chat` (`main`).

## 0. Start here

Suggested opening message for the new chat:

> Read HANDOFF_PHASE2.md, then CLAUDE.md. We are building from the roadmap in section 7. Do not implement anything, make paid model calls, or commit until I pick the next item.

First steps for the new chat:
1. Read this file and `CLAUDE.md`. Inspect `git status --short --branch` and `git log --oneline -8` (git may not be on PATH; see section 9).
2. Check whether the user's running app is stale (section 9: restart rules).
3. Ask which roadmap item to take next. Nothing here authorizes a specific feature.

## 1. Purpose and rationale

Privacy LLM is a **local-first personal assistant** for one user. The reason it exists: use strong hosted models (through OpenRouter) **without sending raw personal or business details to the provider**. Everything the user types or uploads is pseudonymized on their machine before it leaves, and the answer is restored locally.

Goals, in the user's words and decisions:
- A **ChatGPT/Claude-style chat experience** (streaming, uploads, artifacts, branches, themes, shortcuts), built incrementally from a ranked list of capabilities (section 7) rather than all at once.
- **Executive meeting intelligence**: turn noisy meeting transcripts (mostly from Telegram) into useful analysis, then answer questions against the *full* transcript.
- **Privacy first, honest about limits**: this is pseudonymization, not anonymity. The design never claims more than it delivers.
- **Small and understandable**: Python, FastAPI, plain browser JavaScript, JSON/text files. No database, no framework, no containers.
- **Cost control**: a hard monthly ceiling (default US$2.00) enforced before every paid call.

Single user, single machine, loopback only (`127.0.0.1:8000`), no login. Keep it that way unless the user explicitly changes scope.

## 2. Current state at a glance

Committed on `main` (all pushed):
| Commit | What |
|---|---|
| `ed0f381` | Initial codebase (Meetings & Chat, Telegram, Adviser, Persona Library, budgets, retired Memory) |
| `c370241` | Streaming, uploads with OCR, Models page, chat actions, themes, Artifacts, `CLAUDE.md` |
| `b06a590` | Mermaid auto-repair and a fix-it flow |
| `b56a0b0` | 16,000-token streamed limit, truncation detection, Regenerate compactly |
| `cbb0fba` | Artifact hardening: scripts off after uploads, redirect tags stripped, frame loads via `about:blank` |

**Uncommitted at the time of writing** (verify with `git status`):
- `prompts/artifacts.md` rewritten: **HTML is the default artifact format for any deliverable**, diagrams default to HTML/SVG not Mermaid, Word/Excel/PowerPoint requests get a stand-in (Markdown / CSV / HTML slides).
- `tests/test_chat_actions.py` updated to match that prompt.
- This file (`HANDOFF_PHASE2.md`) and a pointer line in `CLAUDE.md`.

The HTML-default prompt was verified against the free saved model (no cost): a trip itinerary became an HTML artifact; a one-sentence factual question stayed as chat; "draw the architecture of a simple web app" became an HTML artifact (not Mermaid); "make me an Excel budget" became a CSV code artifact (it did not say explicitly that real Excel files are unsupported). 88 Python tests pass.

## 3. Architecture

```
Browser UI (static/)           Telegram bot (telegram_transport.py)     Persona CRUD (adviser_api.py)
        \                              |                                    /
         +---------------------- app.py (FastAPI) ---------------------------+
              routes, security headers, one process-wide asyncio.Lock
                                   |
                  engine.py  (chat, streaming, meetings, titles)
                    |            |             |
        privacy_engine.py   documents.py   adviser.py
   (terms + Presidio/spaCy,   (local extraction + OCR,
    reversible tokens)         nothing stored)
                    |
   monthly_budget.py / dev_budget.py  (reserve before every paid call)
                    |
              OpenRouter API   <- the only external model call; sanitized text only
                    |
   storage.py -> JSON/text files under data/ (sessions, settings, budget ledgers)
```

### Request path for one chat message (web, streamed)
1. Browser POSTs `/api/chat/stream` (`message`, optional extracted `attachments`, optional `edit_index`). The route holds the shared `chat_lock` for the whole stream.
2. `engine.stream_chat` builds history, applies **sensitive terms first, then Presidio/spaCy PII detection**, replacing matches with semantic tokens (`PERSON__1`, `LOCATION__2`, `CLIENT__3`...). Token maps are **per session**, stored in the session file, never shared.
3. System prompt = base assistant prompt + `prompts/artifacts.md` (read from disk on every request) + the user's custom instructions (also tokenized).
4. `stream_complete` reserves cost in the monthly ledger, streams from OpenRouter, yields text deltas, and finally a `StreamEnd(finish_reason)`.
5. `StreamRehydrator` restores tokens **even when split across chunks** (holds back a trailing word). Real values only ever exist locally.
6. The reply is saved with `model`, and `truncated: true` if the provider stopped at the output limit, or `stopped: true` if the user pressed Stop (the partial reply is kept). A local title is set after the first reply.
7. NDJSON events to the browser: `start`, `delta`, `done` (public session, no token map), `error`.

Telegram uses the older non-streaming path (`engine.chat`, `ask_meeting`, `analyze_meeting`) and the same privacy and budget code.

### Modules
| File | Role |
|---|---|
| `app.py` | FastAPI app, middleware (origin check, security headers, CSP), all web routes, the sandboxed `/artifact-frame` page, shared lock |
| `engine.py` | Chat, streaming (`stream_complete`, `_stream_turn`, `StreamEnd`, `output_cap`, `STREAM_MAX_TOKENS=16000`), meeting analysis and Q&A, local titles, model selection (`meeting_model()`), per-channel custom instructions |
| `privacy_engine.py` | Terms parsing, Presidio/spaCy (`en_core_web_sm`, offline), tokenization, `rehydrate`, `StreamRehydrator` |
| `documents.py` | Upload extraction: PDF text layer, scanned pages and images via rapidocr (English + Traditional Chinese), DOCX, text/CSV, HEIC via pillow-heif. All limits in `DEFAULT_LIMITS` |
| `storage.py` | Atomic JSON writes, settings, sessions (`list_sessions`, `set_session_flags`, `branch_session`, `rename_session`) |
| `monthly_budget.py` | Calendar-month ledger with durable reservations, per-model recording, `spend_by_model()` |
| `dev_budget.py` | Development spend guard shared with the monthly guard |
| `telegram_transport.py` | Authorization, `/start /help /terms /title /new /meeting /ask`, meeting capture batching (5 s idle), text only |
| `adviser.py`, `adviser_api.py`, `persona_store.py` | Adviser Chat and Persona Library (two built-in personas, user personas) |
| `memory_api.py` | Retired Memory: placeholder endpoints returning 501 |
| `prompts/` | `meeting_assistant.md`, `ask.md`, `artifacts.md` (all read at runtime) |
| `static/chat.js` | Main UI: streaming, message actions, sidebar (search, pin/archive), uploads, shortcuts, export |
| `static/artifacts.js` | Artifact parser, side pane, sandbox frame handling, Mermaid repair |
| `static/models.*`, `settings.*`, `adviser.*`, `navigation.js`, `theme.js`, `sidebar-resize.js`, `style.css` | Other pages and shared UI. `static/vendor/` holds marked, DOMPurify and mermaid with sources in its README |

### Web routes (summary)
Pages: `/`, `/settings`, `/models`, `/adviser`, `/memory`, `/artifact-frame`.
Chat: `POST /api/chat/stream` (main), `POST /api/chat` (non-streaming), `POST /api/upload`.
Sessions: `GET /api/sessions` (`?q=`), `GET/PATCH/DELETE /api/sessions/{id}`, `POST /api/sessions/{id}/branch`. PATCH accepts `title`, `pinned`, `archived`.
Settings and models: `GET/PUT /api/settings`, `PUT /api/settings/model` (`purpose` = general or meeting), `PUT /api/settings/favourite`, `GET /api/openrouter/models`, `POST /api/openrouter/test`, `GET /api/spend/models`.
Adviser: `/api/adviser/...` and the persona router. Memory: placeholders only.

### Storage (all under `data/`, git-ignored, private)
Session files (`data/sessions/*.json`): `id, title, title_generated, created_at, updated_at, channel_origin, session_type (chat|meeting), messages, token_map, pinned, archived, branched_from` and for meetings `transcript, intelligence, mode`. Message fields: `role, content, model, stopped, truncated, typed, attachments`. `settings.json`: `model, meeting_model, chat_diagnosis, monthly_cost_ceiling_usd, favourites, web_instructions, telegram_instructions, review_extracted_text, upload_limits`. `data/monthly_budget/ledger.json`: entries with `model`, `reserved_usd`, `charged_usd`, `state`. Never read, print or commit anything under `data/`, `.env`, `config.json` or `sensitive_terms.txt`.

## 4. Capabilities (what works today)

**Chat (web):** streaming with Stop; rename; search over titles, bodies and transcripts; Markdown export (artifacts exported as fenced blocks); custom instructions (web and Telegram separate); local titles after the first reply; edit-and-resend (cuts the chat back to that message, nothing saved until the new reply succeeds); branch into a new conversation (own copy of the token map); copy buttons on messages and code blocks; pin and archive; light/dark/system theme; keyboard shortcuts (`?` lists them); resizable sidebar; regenerate-compactly when a reply hits the length limit.

**Uploads:** PDF, DOCX, TXT, MD, CSV, PNG, JPG, WebP, BMP, HEIC. Attach button, paste, drag-and-drop. Text is extracted on this machine; scans and images use local OCR. Only the extracted text goes to the model, masked like typed text. Optional preview-and-edit of OCR text (Settings). Limits: 10 MB per file, 20 PDF pages, 5 files, 120,000 characters per file, 200,000 per message. **Images are never sent to a model as pixels.**

**Meetings:** Telegram captures batched transcript text; the shared engine produces executive-style analysis; Q&A always uses the full stored transcript. Optional separate meeting model. Meeting sessions cannot be edited or branched.

**Models and spend:** `/models` page lists the live OpenRouter catalogue with price filters, sorting and favourites; "Use" and "Meetings" buttons; per-model "spent this month" from the ledger. Monthly ceiling default US$2.00.

**Adviser Chat and Persona Library:** conversations with selectable personas; no automatic retrieval of meetings or data.

**Artifacts (web only):** see section 5.

**Telegram:** text chat, meeting capture, `/ask`, titles. No files, photos, streaming or local titles yet.

## 5. Artifacts: design in detail

- **Protocol:** the model wraps standalone content in `<artifact type=... title=... id=...>` ... `</artifact>`. Types: `html`, `svg`, `markdown`, `mermaid`, `code` (with `language`). Reusing an `id` makes a new version. `static/artifacts.js` parses messages, shows cards in the chat and a side pane (live source while streaming, rendered preview once saved).
- **Default policy (latest decision):** any *deliverable* (diagram, page, app, report, dashboard, plan, comparison, checklist, document) is an HTML artifact without the user saying "artifact". Quick answers, explanations and short snippets stay in chat. Diagrams default to HTML/SVG; Mermaid, SVG, Markdown or code only when asked. Word/Excel/PowerPoint are not supported yet, so the model states that and makes a stand-in (Markdown for Word, CSV for Excel, HTML slides for PowerPoint). HTML must be self-contained, responsive, readable, and still make sense with scripts blocked.
- **Privacy:** the model writes artifacts with placeholders; real values are restored locally before display. Downloads and exports therefore contain real, unmasked text (the UI says so).
- **Sandbox:** HTML/SVG render in an iframe pointing at `/artifact-frame`, which has its own CSP (`default-src 'none'`, inline scripts only, no network, `sandbox allow-scripts`, no `allow-same-origin`). The parent posts the document into the frame after a handshake. Mermaid's library is inlined into the frame document because external scripts failed to load there.
- **Hardening:** "Run scripts" starts **off in any conversation that contains uploads** (uploaded files can carry hidden instructions); meta refresh, `<base>` and resource-hint tags are stripped; every frame load goes through `about:blank` first (a frame whose document was replaced with `document.write` did not reliably navigate to a new URL).
- **Mermaid:** source is tried as written, then with `\n` turned into `<br/>` and parenthesised labels quoted; if both fail the user sees the parse error and an "Ask the model to fix it" button.
- **Limits:** streamed replies allow 16,000 output tokens (or the model's own maximum). A cut-off artifact shows a note and "Regenerate compactly". Later turns re-send earlier artifacts in full (cost).
- **Rules:** never weaken the `/artifact-frame` policy, never add `allow-same-origin`, never send images to a model without an explicit scoped decision.

## 6. Design choices and why

| Choice | Why |
|---|---|
| Local-first, JSON/text files, no database | Matches a single-user tool; easy to inspect, back up and keep private. Atomic writes are enough |
| Pseudonymize with reversible, per-session tokens | Models still get useful structure; real values never leave. Per-session maps avoid cross-chat linkage. This is **pseudonymization, not anonymity**; context can still identify people |
| Terms list first, then Presidio/spaCy | The user can name things a detector misses (codenames, clients). Detector is English-first and offline |
| Rehydrate locally, including inside artifacts | The user sees real content; the provider never does |
| Images and scans become text locally (OCR) | Pixels cannot be masked. Real vision input is descoped for privacy |
| Streaming is web-only | Telegram does not need it; keeps the old path stable |
| Single shared `asyncio.Lock`, single worker | Simple ordering of file writes; no database transactions needed |
| Reserve cost before each call, refuse rather than shorten | Hard spending ceiling; a larger output limit raises the reservation, so requests that do not fit are refused with a clear message |
| Local titles (first line of the first message) | No extra model call or cost; stable once set; manual renames are never overwritten |
| Separate optional meeting model | Meeting analysis is long and benefits from a different model than casual chat |
| Edit truncates in memory and saves only on success | A failed edit leaves the saved chat untouched |
| Branch copies messages and a copy of the token map into a new web session | Independent conversations; no cross-contamination |
| Artifacts only in streamed web chat | Telegram cannot render them; meetings are not deliverable-oriented |
| Artifact prompt read from disk each request | Behaviour can be tuned without restarting the app |
| Memory retired (placeholder page, 501) | The previous implementation was judged not useful; do not restore or replace it unless asked |
| Voice input/output descoped | User decision |
| No React, LangChain, queues, Docker, vector DB | Keep it small; see `CLAUDE.md` |

## 7. Roadmap: the tiered list (build in this order of difficulty)

Status: DONE, PARTIAL, TODO, BLOCKED (needs a decision), OUT (descoped or recommended out).

**Tier 1: small**
| Capability | Status | Notes |
|---|---|---|
| Streaming and Stop | DONE | Web only |
| Rename, search, Markdown export | DONE | |
| Custom instructions | DONE | Per channel |
| Model switching, reply labels, Models page | DONE | |
| Local titles | DONE | Web only |
| Resizable sidebar | DONE | |
| Edit and resend | DONE | Normal chats |
| Copy buttons | DONE | |
| Pin / archive | DONE | |
| Theme, keyboard shortcuts | DONE | |
| Per-model spend | DONE | |
| Regenerate last reply | TODO | |
| Prompt templates | TODO | |
| PDF export, share | TODO | Markdown export exists |

**Tier 2: moderate**
| Capability | Status | Notes |
|---|---|---|
| File upload and OCR | DONE | Web only |
| Paste, drag-and-drop, HEIC | PARTIAL | Built; paste and drop not browser-tested; user has not tested HEIC |
| Branch into new conversation | DONE | Alternative replies inside one chat not built |
| Artifacts / canvas pane | PARTIAL | Built and hardened; HTML default policy awaiting commit; Mermaid confirmed in the user's own browser, other types not yet confirmed there |
| Context-window management | TODO | Full history is sent every turn; artifacts are re-sent in full |
| Projects (shared instructions/files) | TODO | Needs a decision on token-map sharing |
| Telegram parity (files, photos, streaming, local titles, chat actions) | TODO | `documents.py` is transport-independent and can be reused |
| Meeting skill upgrade | TODO | Structured decisions, owners, re-analysis |
| Voice input/output | OUT | Descoped by the user |

**Tier 3: significant**
| Capability | Status | Notes |
|---|---|---|
| Tool-calling loop | TODO | Foundation for the rest of this tier; needs `CLAUDE.md` rule lifted for it; budget check per loop, iteration cap, tokenized arguments and results |
| Skills system | TODO | Instruction folders loaded on demand |
| Word / PowerPoint generation | TODO | Model returns a token-based spec; python-docx / python-pptx render locally with real values. Fits the privacy design best |
| Excel/CSV analysis | TODO | Parse locally, send schema and sample rows only |
| Image generation | TODO | |
| Web search with citations | BLOCKED | The model writes queries that go to a third party; needs a query-privacy policy (approve each query, tokens only, or allow-list) |

**Tier 4: hard**
| Capability | Status | Notes |
|---|---|---|
| Memory layer | BLOCKED | Needs a decision on shared identities across sessions (a global identity map breaks per-session isolation) |
| Deep research | BLOCKED | Depends on search and the tool loop |
| Scheduled / background tasks | TODO | |
| Connectors (Gmail, Calendar, Drive) | TODO | Gmail ingestion is in the original contract but not built |
| Code execution sandbox | OUT | Conflicts with the no-Docker rule |

**Tier 5: open-ended (recommend out)**
Browser/computer control, multi-agent orchestration, multi-user accounts and hosting, collaboration, native apps, self-hosted models, true vision input.

**Suggested next steps (not authorized yet):**
1. Commit the HTML-default prompt change, then close the remaining Artifacts verification (user opens an HTML, SVG and Markdown artifact in their own browser; toggle scripts; switch versions).
2. Finish Tier 1: regenerate and prompt templates (small).
3. Tier 2: context-window management, then Telegram parity.
4. Tier 3: tool-calling loop, then skills plus Word/PowerPoint generation (replaces the Office stand-ins), then CSV analysis.
5. Web search and Memory only after their privacy decisions.

## 8. Known issues, limits and unverified items

- **Place names get tokenized.** Presidio masks locations, so "What is the capital of France?" reached the model as a placeholder and the free model answered that it could not determine the place. General-knowledge questions involving countries or cities degrade. This is a design question for the user (for example allow common public place names, or detect only private locations), not a bug in artifacts.
- **Free saved model quality.** The user's saved model is a free Nemotron model: fine for tests, weaker at instructions and Mermaid syntax. Format compliance for artifacts depends on the model.
- **Unverified in the user's real browser:** HTML, SVG and Markdown artifact previews, the "Run scripts" toggle, and frame reloads after the `about:blank` fix. Mermaid preview was confirmed by the user before the latest hardening.
- **Possible frame navigation leak.** With scripts on, an artifact script may be able to navigate its own frame to an external address, carrying restored real text in the URL. It could not be reproduced in the built-in browser. Scripts default off after uploads; link clicks inside artifacts remain possible.
- **Telegram:** text only; unanalyzed meeting captures are lost on restart; restart can process queued updates.
- **Privacy limits:** detection is English-first and can miss names; session files are plaintext on disk; the provider can still infer from context.
- **Cost:** a 16,000-token reservation uses more of the ceiling per request; expensive models may be refused when the budget is low. Re-sending artifacts in history raises cost.
- **No Node** is installed in the dev shell, so `tests/*.cjs` browser checks cannot run there. JavaScript is verified by the manual scratch-copy method (section 9).
- **Stale historical files** (reports, logs, screenshots, runners) sit untracked in the checkout. Do not bulk-add them.

## 9. Working agreements and dev playbook

Rules (see `CLAUDE.md`): never read or print `data/`, `.env`, `config.json`, `sensitive_terms.txt`; stage files by name, review staged names and scan for secrets before every commit; **commit locally without asking** once a change set is finished and tests pass (the user's standing decision; GitHub is their safeguard), but **push only when asked**; no history rewriting, amending or resetting unless asked; do not weaken the artifact sandbox; Memory stays retired unless asked.

Environment:
- Windows, PowerShell, Python 3.12 venv at `.venv`. Run tests: `.\.venv\Scripts\python.exe -m unittest discover -s tests` (88 tests, all mocked, about 15 s).
- **git** is not on PATH in the desktop shell. A bundled copy exists, for example `%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe`. No author is configured; commits so far used `-c user.name=jjjsg999 -c user.email=jjjsg999@users.noreply.github.com` to avoid exposing a personal email. Multi-line commit messages with double quotes break PowerShell arguments, so write the message to a file and use `git commit -F`.
- **Restart rules for the user's live app** (`Start Privacy LLM.bat`, port 8000): Python changes (`app.py`, `engine.py`, ...) need a restart; files under `static/` and `prompts/` are read fresh (hard-refresh the page). A stale server once made artifacts silently not trigger because it predated the prompt code. Restarting interrupts Telegram, so ask first.
- **Reading chats for debugging:** use the live app API (`GET /api/sessions`, `/api/sessions/{id}`), decode the raw bytes as UTF-8 (`Invoke-RestMethod` mangles emoji in PowerShell 5.1), and read only the chat the user named.

Verification method used throughout:
1. Backend: add small offline unit tests that mock the provider; keep them in `tests/`.
2. UI: copy the project to a scratch folder under the session scratchpad (code, `static/`, `prompts/`, an empty `data/`), run uvicorn on a spare port with `TELEGRAM_BOT_TOKEN=` and no API key, write sample session JSON files (UTF-8 **without** BOM), and drive the page with the built-in browser. For model-dependent paths, append a fake `stream_complete` to the scratch `engine.py`. Delete the scratch folder afterwards.
3. Built-in browser quirks: iframes with the `sandbox` attribute do not run scripts there (the scratch copy removes the attribute and relies on the CSP `sandbox` header; real files keep both); screenshots of the artifact frame are often blank on the first capture, take a second one; `javascript_tool` calls time out at about 45 s.
4. Real-model checks only when asked, with the free or a cheap model, using the real app on a spare port (`TELEGRAM_BOT_TOKEN=` set empty so Telegram does not poll), then delete the test chats via the API and leave settings unchanged.
5. Report honestly what was and was not verified.

## 10. Open decisions the user will need to make

- **Web search:** how queries are handled (approve each, tokens only, allow-list).
- **Memory:** shared identity map across sessions or not; storage form.
- **Projects:** whether chats in a project share one token map.
- **Place-name masking:** keep masking all locations, or let common public places through.
- **Telegram parity scope:** which of files, photos, streaming and local titles to port first.
- **Lifting `CLAUDE.md` rules** for tool calling and any retrieval before Tier 3 and 4 work starts.
