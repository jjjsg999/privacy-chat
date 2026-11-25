# Privacy LLM: Planning Handoff for the Next Phases

**Written 2026-11-23**, from the code at commit `ca6891d`. Use this to start a separate chat that plans the next phases of the app. It covers product and engineering only. **Interface design is deliberately excluded**; it has its own document, `UI_HANDOFF.md`.

It builds on `HANDOFF_PHASE2.md` (2026-11-22), which remains the reference for architecture, request flow, artifacts and the dev playbook. This file records what changed since then, the current state, what was learned, the remaining roadmap, candidate next phases with trade-offs, and the decisions only you can make. Where documents disagree, the source code wins, then this file, then `HANDOFF_PHASE2.md`. `CLAUDE.md` holds the standing rules.

---

## 0. How to use this document

Suggested opening message for the planning chat:

> Read HANDOFF_PHASE3.md, then CLAUDE.md and HANDOFF_PHASE2.md. We are planning the next phases. Do not implement anything yet. Start by asking me which of the open decisions in section 9 I want to settle, then propose a sequenced plan with scope, risks and acceptance checks for each phase.

The planning chat should:
1. Read this file, `CLAUDE.md`, and skim `HANDOFF_PHASE2.md` sections 3 to 9.
2. Run `git log --oneline -15` and `git status --short --branch` (git is not on PATH; see section 10).
3. Treat section 7 (candidate phases) as input, not a decision. Nothing here authorizes a specific feature.

---

## 1. Product recap (unchanged)

A **local-first personal assistant** for one user. It exists to use strong hosted models (through OpenRouter) **without sending raw personal or business details**: everything typed or uploaded is pseudonymized on this machine before it leaves, and answers are restored locally. It is **pseudonymization, not anonymity**, and the app says so. Single user, single machine, loopback only (`127.0.0.1:8000`), no login.

Main jobs: everyday chat with files and generated documents (artifacts); **executive meeting intelligence** from noisy Telegram transcripts, with Q&A over the full transcript; Adviser chat with personas; a hard monthly cost ceiling enforced before every paid call.

Non-negotiables (from `CLAUDE.md`): Python, FastAPI, plain browser JavaScript, JSON and text files; no React, database, Docker, queues, LangChain or LiteLLM; web and Telegram are transports and all privacy and model logic lives in the shared engine; never commit or print `.env`, `config.json`, `sensitive_terms.txt` or `data/`; tests mock the provider; commit locally without asking, push only when asked.

---

## 2. State at a glance

**Repository:** `https://github.com/jjjsg999/privacy-chat`, branch `main`. Pushed up to `06935af`. **Two local commits are not pushed:** `9fc3f5f` (UI handoff) and `ca6891d` (spaces). **187 tests pass** (`.\.venv\Scripts\python.exe -m unittest discover -s tests`, about 22 seconds, all mocked).

**Commits since the Phase 2 handoff (all 2026-11-23):**

| Commit | What |
|---|---|
| `f75535c` | Layered privacy filter (off by default) plus the precision/recall evaluation harness |
| `1fb8430` | CJK boundary fix: placeholders and terms glued to Chinese text now match |
| `7a2f6b9` | `place_names` setting: public countries, regions and major cities pass through by default |
| `f5688cb` | Context management: masked-text cache, context builder, keep-in-context pins, "left out" note |
| `13c4c7b` / `3167978` | Structured meeting record and action tracker, **then reverted** (section 5) |
| `70c9f93` | Fix: `/new` and `/meeting` waited for a running analysis and let it post |
| `06935af` | Named model failures (timeout, empty reply, HTTP code); meeting calls wait up to 300 s |
| `9fc3f5f` | `UI_HANDOFF.md` |
| `ca6891d` | Spaces: Work, Personal and your own, to group chats |

**Running app:** started from a Claude session with `.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000` as a background process (also startable with `Start Privacy LLM.bat`). Restarting it interrupts Telegram and **discards any in-memory meeting capture**.

---

## 3. What exists now (capabilities, by area)

### 3.1 Privacy engine (`privacy_engine.py`, `public_places.py`)

- **Standard mode (default in code):** sensitive terms first, then Presidio with `en_core_web_sm` (spaCy). Semantic reversible tokens (`PERSON__1`, `CLIENT__2`, ...) with a per-chat token map.
- **Layered mode** (`privacy_detectors = "layered"`): priority terms list > Presidio pattern recognizers > **OpenAI Privacy Filter** (Apache 2.0) > spaCy, merged by union so a value is never partly masked. Privacy Filter runs on **ONNX Runtime int8, no PyTorch**; weights are 1.65 GB under `models/privacy-filter/` (git-ignored). Download once with `.\.venv\Scripts\python.exe -m eval.download_privacy_filter`. **Fails closed**: if the model cannot load, the request is refused with a clear error; there is no silent fallback. Privacy Filter's `private_date` output is ignored (dates stay as spaCy handles them). New token kinds: `ACCOUNT_NUMBER`, `ADDRESS`, `SECRET`. Layered mode is **switched on in the user's own settings**; the code default stays `standard`.
- **Evaluation result (invented test set, 550 snippets, 1,323 must-mask values):** standard recall 77.25%, precision 72.21%; layered recall **95.24%**, precision **75.03%** (verdict: adopt). With `place_names = common_public` precision rises to 85.48% (standard) and 86.98% (layered), recall unchanged. Run it with `.\.venv\Scripts\python.exe -m eval.run_privacy_eval` (default `mask_all`, reproducible) or `... common_public`. The numbers must stay identical after any privacy-related change.
- **Where layered is still weak:** Chinese-character and romanized Chinese names (about 38 person misses) and some address formats (about 25 misses; "No. 18, Lane 143" style and some US-style). Remaining false positives are mostly product names, dates and organisation names.
- **`place_names`:** `common_public` (default) lets countries, broad regions and about 300 major cities through so geography questions work; towns, neighbourhoods and addresses stay masked; user terms still win. `mask_all` restores the old behaviour. Lists live at the top of `public_places.py`.
- **CJK handling:** word boundaries treat CJK as separators (placeholders and terms glued to Chinese text). Chinese terms now match inside sentences.
- **Fingerprint:** `PrivacyEngine.fingerprint(terms_text)` hashes the terms list, detector mode, `place_names`, the public-places list and package versions. It drives the masked-text cache.
- **Known quirk:** placeholder-shaped examples in the prompt files (for example "PERSON__1") get reserved as `LITERAL__n` tokens before sending, because prompts pass through the same tokenizer. Harmless; cosmetic.
- **Date masking is an open decision** (section 9): dates are masked by spaCy `DATE_TIME` in both modes today; Privacy Filter dates are ignored.

### 3.2 Context management (`context_builder.py`, `engine.py`)

- **Masked-text cache:** each message's masked text (attachments included) is stored in the session with the fingerprint; only messages without a valid cache go through the filter. A changed fingerprint (for example a new term) re-masks everything once, reusing the token map. Branching copies the cache; edit-and-resend keeps it for kept messages. Measured on a 50,000-character invented transcript: first pass 1.85 s standard / 12.4 s layered; cached pass about 1.2–1.7 ms.
- **One builder used by every path** (web streaming, web non-streaming, Telegram chat, Telegram `/ask`, meeting analysis and questions, Adviser). Settings `context_mode_chat` (default `balanced`) and `context_mode_meeting` (default `full`). Caps: lean 12,000 tokens, balanced 30,000, full = the model limit. Model limit = context length minus the output reservation minus the system prompt minus a 10% margin; the output reservation is capped at a quarter of the window for small models. Context length unknown (catalogue not yet cached) is assumed to be 32,000, and the note says so.
- **Always sent:** system prompt and instructions, the new message, the previous exchange, a short first message, the most recently updated artifact, every pin, and the full meeting transcript. **Cut order:** superseded artifact versions, latest versions of other artifacts, older uploads (one-line `[file ... not included]` stubs), oldest messages in user/assistant pairs. A system note tells the model what was left out and to ask the user to pin it. If the protected items alone exceed the model limit, **nothing is sent or charged** and a clear message is shown.
- **Pins** (messages, files, artifacts) and the muted "Sent N messages · M left out" line exist. The context record is stored on a reply **only when something was cut**; `prompt_tokens` from the provider is stored next to our estimate (streaming path only) for later calibration of the token estimator.
- **Savings (estimated, per whole chat):** 40-turn chat lean 54% / balanced 14% less; large upload lean 59%; artifact edited six times lean 39%; meeting lean 16% / balanced 5%. Full equals the old behaviour. Reproduce with `.\.venv\Scripts\python.exe -m eval.context_report`.
- **Model context lengths** are cached in memory for five minutes and on disk in `data/model_context.json` (written whenever the catalogue is fetched).
- **Deliberately not built:** model-written summaries and any extra model call for context.

### 3.3 Spaces (`storage.py`, `app.py`, `static/spaces-settings.js`)

- Settings: `spaces: [{id, name}]` (default Work and Personal) and `telegram_space`. Session files get a `space` id. A chat with no `space`, or an unknown one, is treated as Work **at read time**; the field is written the next time the chat is saved for any reason. No migration script rewrote any file.
- Sidebar shows one space at a time, remembered per device; new chats take the current space; branches keep the original's; "Move to..." in each chat's menu; search covers the current space with an "All spaces" toggle.
- Telegram chats and meetings go to `telegram_space`. Settings has a Spaces section (add, rename, delete with reasons, Telegram destination). Delete is refused if the space has chats (archived included), is the last space, or is the Telegram destination.
- API: `GET /api/sessions?space=<id>|all`, `PATCH /api/sessions/{id}` accepts `space`, and `GET/POST/PATCH/DELETE /api/spaces`, `PUT /api/spaces/telegram`.
- Spaces organise only: token maps stay one per chat, the terms list is global, the context builder is unchanged. **Adviser conversations are stored separately and do not have spaces.**
- **Not built because the underlying features were rolled back:** a space filter on the action tracker and `/actions all` on Telegram.

### 3.4 Chat, uploads, artifacts (from Phase 2, unchanged)

Streaming with Stop, edit-and-resend, branching, pin and archive of chats, search, Markdown export, custom instructions per channel, local titles, uploads (PDF, DOCX, text, images) with local OCR, artifacts (HTML by default) in a sandboxed iframe, Mermaid repair, truncation detection with "Regenerate compactly". See `HANDOFF_PHASE2.md` sections 3 to 5.

### 3.5 Models, budget, spend

Live OpenRouter catalogue with filters; separate optional meeting model; per-model spend; a hard monthly ceiling reserved before every call (`monthly_budget.py`); `dev_budget.py` guards development calls. Model failures now say what went wrong (timeout, empty reply, HTTP code) without echoing provider text. Meeting analysis and questions wait up to 300 seconds; other calls 90.

### 3.6 Telegram (`telegram_transport.py`)

Authorized private chats only. Commands: `/start`, `/help`, `/terms`, `/title`, `/new`, `/meeting`, `/ask`. Default: the first message starts a meeting capture, analysed after five seconds of silence (chunks batched). Failure notice now ends with a "Reason:" for model errors. **`/new` and `/meeting` now cancel a pending or running analysis immediately** (they used to wait for it and post it). Text only: no files, photos, streaming, local titles or chat actions yet.

---

## 4. Architecture notes that matter for planning

- **Request path:** transport → `app.py` / `telegram_transport.py` → `engine.py` (chat, streaming, meetings, titles, context) → `privacy_engine.py` (mask) → provider call via `complete()` / `stream_complete()` with budget reservation → rehydrate locally. Details in `HANDOFF_PHASE2.md` section 3.
- **Storage:** JSON files under `data/` (sessions, settings, ledgers, adviser sessions, persona store), written atomically. One process-wide `asyncio.Lock`; one worker. Session extras added recently: masked-text caches (`masked`, `transcript_masked`, `intelligence_masked`, never sent to the browser), `pinned` flags, `pinned_artifacts`, `space`, optional `context` record.
- **Settings keys (in `settings.json`):** `model`, `meeting_model`, `favourites`, `chat_diagnosis`, `review_extracted_text`, `web_instructions`, `telegram_instructions`, `monthly_cost_ceiling_usd`, `upload_limits`, `privacy_detectors`, `place_names`, `context_mode_chat`, `context_mode_meeting`, `spaces`, `telegram_space`. The terms list is a separate file (`sensitive_terms.txt`).
- **Prompts** are read from disk on every request (`prompts/meeting_assistant.md`, `ask.md`, `artifacts.md`), so prompt edits take effect without a restart. **Python changes need a restart.**
- **Eval and tooling under `eval/`:** privacy evaluation (generator, harness, results), context report, model download script. Results are committed under `eval/results/`; re-running rewrites them with new run times.

---

## 5. The meeting-skill upgrade: built, rolled back, still unresolved

**What was built (commit `13c4c7b`, still in history):** one model call returns a JSON meeting record (brief, decisions, actions with owner and due date, open questions, risks, key figures), validated with pydantic, retried once, with a prose fallback; transcript lines numbered L1..Ln with line numbers surviving masking; local verification of every item (cited lines exist, owner and figures appear in the cited lines) with flags rather than drops; Telegram summary and `/actions`; an action tracker (status stored apart from the record, `/actions` page and API); Q&A citing `[L12]`; and a spend-capped extraction test (`eval/run_meeting_eval.py`) tested only against a fake model.

**What happened:** the user's real Telegram meeting analysis then failed ("Meeting processing or delivery failed"). The upgrade was suspected and **rolled back entirely** (`3167978`, a revert, so it is recoverable with `git revert 3167978` or `git show 13c4c7b`). The Telegram meeting analysis then worked.

**What is not known:** the root cause was never established. The server log only showed `ChatError`. Candidates, none proven: the new JSON prompt producing longer or empty output on the user's free reasoning model; the 90-second read timeout on a long transcript (since raised to 300 seconds for meeting calls); an unrelated `/new` bug found along the way (fixed, `70c9f93`). A live test with the user's configured model on a short invented transcript did return a valid structured record in about 12 seconds, so the code path itself worked.

**Lessons for planning:**
1. **Mocked tests cannot catch prompt or model-behaviour failures.** The upgrade passed 205 tests and failed in real use. Any feature that depends on a model's output shape needs a **real-model smoke test before the user's running app is restarted onto it**, and ideally a feature flag so it can be switched off without a revert.
2. The failure notice is now informative; use it. A "Reason:" line will say whether a retry is a timeout, an empty reply or an HTTP error.
3. To re-attempt, first run a real-model extraction test (the harness exists in history) and decide the fallback behaviour when the model returns prose.

**Reusable pieces if it returns:** the reverted code has the schema, verification rules, numbered-transcript masking guard, Telegram summary splitter, due-date parser and tracker API, all with tests.

---

## 6. Known issues, limits and technical debt

**Privacy and data**
- Detection is English-first. Layered mode improves recall but Chinese names and some addresses are still missed (section 3.1). The evaluation set is **invented**; real-world recall will differ.
- Session files are plaintext on disk; there is no encryption at rest.
- Pseudonymization only: context can still identify people. The provider sees non-private places and any unmasked words.
- Placeholder numbering: prompt examples allocate `LITERAL__n` tokens (cosmetic).
- Adviser conversations have their own small history bound (last 12 messages, 4,000 characters each), apply the saved detector mode, and now use the context builder; they have no cache and no spaces.

**Telegram**
- **The capture buffer is memory-only.** A restart or a failed analysis can lose a transcript; a failed analysis keeps the chunks in memory only until a restart. (An idea to persist it was discussed but **never built**.)
- `drop_pending_updates=False`: after a restart Telegram can re-deliver unconfirmed messages, which could re-ingest an earlier transcript. No duplicate-update protection exists. Unproven as a cause of any observed failure.
- Text only; no streaming; titles use the older third-message model call.

**Model and cost**
- The saved model may be a free reasoning model: fine for tests, variable on long structured output and instructions.
- Streaming reserves cost for 16,000 output tokens; expensive models may be refused when the budget is low.
- Layered detection is slow on first pass for long text (about 12 s per 50,000 characters) and loads a 1.65 GB model on first use (about 2 s).

**Engineering**
- No Node in the dev shell, so `tests/*.cjs` browser checks cannot run; JavaScript is verified manually with a scratch copy and the built-in browser.
- Python tests do not cover CSS or HTML ids; changing an id used by `chat.js`, `settings.js` or `models.js` breaks pages without failing a Python test.
- `static/settings.js` is not valid UTF-8 (single-byte character); edit it byte-safely. Source files are mostly CRLF; the commit message of `f75535c` has a stray byte-order mark.
- Memory is retired (placeholder page, 501 endpoints); `memory_api.py` and a nav entry remain.
- Stray untracked reports, logs and runner scripts from earlier phases sit in the checkout; never bulk-add them. `retirement_test_runner.py` is a leftover.
- Several `app.py` and `storage.py` areas now carry features added in quick succession; a consolidation or light refactor pass may pay off before the next large feature.

---

## 7. Roadmap status (updated from `HANDOFF_PHASE2.md` section 7)

| Capability | Status | Notes |
|---|---|---|
| Streaming, chat actions, uploads and OCR, artifacts, Models page | DONE | Phase 2 |
| Layered privacy filter | DONE | Behind a setting; on for the user; evaluated |
| Place-name policy | DONE | `common_public` default |
| Context-window management and pins | DONE | Section 3.2 |
| **Spaces** | DONE | New; organising only |
| Regenerate last reply; prompt templates; PDF export and share | TODO | Tier 1 leftovers (small) |
| Meeting skill upgrade (structured record, tracker) | **ROLLED BACK** | Section 5; code recoverable |
| Telegram capture saved to disk; duplicate-update protection | TODO | Not built; see section 8, candidate B |
| Re-analysis and versions of saved meetings | TODO | Out of scope previously |
| Telegram parity (files, photos, streaming, local titles, chat actions) | TODO | `documents.py` is reusable |
| Projects (shared instructions and files) | TODO | Needs a token-map-sharing decision; spaces are not projects |
| Tool-calling loop; skills system | TODO | Needs a `CLAUDE.md` rule lifted |
| Word and PowerPoint generation | TODO | Token-based spec, local rendering |
| Excel/CSV analysis | TODO | Parse locally; send schema and sample rows only |
| Web search with citations | BLOCKED | Query-privacy policy needed |
| Memory layer; deep research | BLOCKED | Shared-identity decision; depends on search and tool loop |
| Scheduled and background tasks; connectors (Gmail, Calendar, Drive) | TODO | Gmail ingestion was in the original contract |
| Encryption at rest | TODO | Not yet discussed |
| Voice | OUT | Descoped |
| Code execution sandbox | OUT | Conflicts with the no-Docker rule |

---

## 8. Candidate next phases

Sizes are rough (S = a session or less, M = a few sessions, L = a multi-step project). "Needs" lists decisions or prerequisites.

**A. Reliability and trust for meetings (S–M). Recommended first.**
Run the real-model extraction test (restore `eval/run_meeting_eval.py` from history) to see how the user's model behaves on structured output before touching meeting features again. Add the **named failure reasons** to other Telegram flows. Add a **real-model smoke test** step to the working agreement for any prompt-dependent change. Optionally add a simple **feature flag** pattern (a setting) so risky behaviour can be turned off without a revert.

**B. "Stop losing meetings" on Telegram (S–M).**
(1) Save the capture buffer to disk (atomic writes under `data/`, restored on startup, discarded by `/new`). (2) Ignore Telegram updates that were already handled, by persisting the last update id. Include tests for the restart cases and a clear user-visible state for a recovered capture. Avoids the transcript loss seen in practice.

**C. Re-analysis and meeting versions (M).**
Re-run analysis with another model or prompt, keep versions, choose a current one. Best done after A, and it is the natural home for a retry of the structured record.

**D. Return of the structured meeting record and action tracker (M).**
Only after A. Reuse the reverted code (`git show 13c4c7b`), add the space filter to the tracker, feature-flag it, and require a passing real-model extraction test before enabling. Decide up front what to do when the model returns prose (the prior design kept the brief as prose with the reason).

**E. Telegram parity (M).**
Files and photos (reusing `documents.py` and masking the extracted text), streaming replies, local titles, chat actions. Decide the scope order with the user.

**F. Privacy quality work (M, ongoing).**
Improve recall on Chinese names and addresses (language-specific recognizers, pattern recognizers for address formats, or fine-tuning Privacy Filter as its documentation allows); extend the evaluation set with more realistic invented material and track false positives; decide date handling; consider a "show what will be sent" preview for sensitive chats. Any change must keep `eval.run_privacy_eval` at or above 95.24% recall and 75.03% precision.

**G. Office documents and spreadsheets (L).**
Word and PowerPoint generation (model returns a token-based spec; python-docx and python-pptx render locally with real values) replaces the current Markdown and CSV stand-ins. Excel/CSV analysis (parse locally, send only schema and sample rows). Fits the privacy design well; needs a spec format and a decision on what a model may see.

**H. Tool-calling loop, then skills (L). Foundation work.**
Needs `CLAUDE.md` rules lifted (it forbids unnecessary abstraction and constrains tool use). Needs a per-iteration budget check, an iteration cap, tokenized arguments and results, and a safe default tool set. Enables skills (instruction folders loaded on demand) and later search and scheduled tasks.

**I. Web search with citations (M, blocked on a decision).**
The model writes queries that would leave the machine. Options: approve each query, allow only placeholder-safe queries, or an allow-list. Depends on H for a clean implementation.

**J. Projects (M).**
Shared instructions and files across chats. Decide whether chats in a project share one token map (a trade-off between usefulness and per-chat isolation). Spaces already give grouping; projects add shared context and are a different feature.

**K. Memory and shared identity (L, blocked).**
A global identity map breaks per-chat isolation; needs an explicit design and decision. Memory is intentionally retired until then.

**L. Security and operations (S–M).**
Encryption at rest for `data/`; backup, export and import of the whole workspace; log hygiene; a health or status page for the running server; starting the server reliably without a Claude session (the `.bat` exists; the Windows service or Startup folder is untested); dependency and model-download documentation for a clean install; a one-command local test and evaluation script.

**M. Engineering hygiene (S).**
Clean untracked clutter; fix the settings.js encoding; normalise line endings and add `.gitattributes`; consolidate retired Memory code; small refactors of `app.py` and `storage.py`; add lightweight checks that catch broken HTML ids. (UI redesign work belongs in `UI_HANDOFF.md`, not here.)

### Suggested sequencing (for discussion)

1. **A** (reliability groundwork), then **B** (stop losing meetings).
2. **C** and **D** together as "meeting skill, second attempt", gated by a real-model test.
3. **E** or **G**, whichever the user's daily pain favours: meetings and Telegram, or documents.
4. **F** continuously in small steps.
5. **H** only when a feature needs it, then **I** and skills.
6. **L** whenever the user wants the app to run unattended or the data to be safer.

---

## 9. Decisions only the user can make

1. **Date masking:** keep masking dates as today, let non-identifying dates through, or follow Privacy Filter. (Deferred earlier.)
2. **Web search:** how queries are handled (approve each, placeholder-safe only, allow-list), and whether to build the tool loop first.
3. **Memory and Projects:** whether chats may ever share an identity map; whether Projects share a token map.
4. **Telegram:** which parity features first; whether to persist captures and ignore duplicate updates now.
5. **Meetings:** whether to retry the structured record, and what the fallback should be when the model returns prose; how much of the tracker is wanted.
6. **Lifting `CLAUDE.md` rules** needed for the tool loop and any retrieval.
7. **Encryption at rest and unattended running:** wanted or not, and the acceptable recovery story if a key is lost.
8. **Model strategy:** which model for chat, which for meetings, and whether a stronger meeting model is worth the cost; the saved model has been a free one.
9. **Release hygiene:** whether to push the two local commits, tag releases, and adopt a feature-flag habit.
10. **Evaluation realism:** whether to build a small private evaluation set from real (anonymised by the user) material, kept outside the repo.

---

## 10. Working agreements and playbook

- **Rules:** `CLAUDE.md`. In brief: never read `data/`, `.env`, `config.json` or `sensitive_terms.txt`; stage files by name, review staged names, scan for secrets; **commit locally without asking when a change set is finished and tests pass; push only when asked**; never amend, rebase, reset, force-push or rewrite history unless asked; do not weaken the artifact sandbox; Memory stays retired unless asked; add small offline tests and mock the provider; real-model checks only when asked, with a cheap or free model.
- **Run and test:** `.\.venv\Scripts\python.exe -m unittest discover -s tests`; privacy evaluation `.\.venv\Scripts\python.exe -m eval.run_privacy_eval`; context report `.\.venv\Scripts\python.exe -m eval.context_report`.
- **Git:** not on PATH in the desktop shell. A bundled copy: `%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe`. No author is configured; commits have used `-c user.name=jjjsg999 -c user.email=jjjsg999@users.noreply.github.com`. Write multi-line commit messages to a file and use `git commit -F` (write the file without a byte-order mark).
- **Restarts:** Python changes need a restart; `static/` and `prompts/` do not. Restarting interrupts Telegram and loses any in-memory meeting capture, so ask first. The task briefs so far told the assistant not to touch the running app; restarts have been done only when asked.
- **UI verification:** scratch-copy method in `HANDOFF_PHASE2.md` section 9 (copy code, `static/`, `prompts/` and an empty `data/` to a scratch folder, run on a spare port with `TELEGRAM_BOT_TOKEN=` empty, drive with the built-in browser, delete afterwards).
- **Real-model checks:** none are run unless the user asks. Because the meeting upgrade failed only against a real model, plan an explicit, cheap, user-approved real-model smoke test into any phase that changes prompts or output shapes.
- **PowerShell 5.1 gotchas:** no `&&`; `Set-Content -Encoding utf8` writes a BOM; `Invoke-RestMethod` mangles non-ASCII; escape carefully when patching files from PowerShell, or patch with a small Python script that preserves line endings.
- **Related documents:** `UI_HANDOFF.md` (interface; out of scope here), `HANDOFF_PHASE2.md`, `CLAUDE.md`, `PROJECT_HANDOFF_CURRENT.md` (older backend detail), `eval/results/` (latest numbers).

---

## 11. What was not verified while writing this

- The root cause of the original Telegram meeting failure (section 5).
- Behaviour of layered detection and the context builder over a long period of real use; they have been tested with invented text and mocks, plus one short real-model call.
- Telegram behaviour after the spaces change (placement of new Telegram chats in `telegram_space` is covered by tests, not by a live message).
- Whether the user's running server is still up at the time of reading; check `GET http://127.0.0.1:8000/api/spaces`.

---

## 12. Web search (built 2026-11-25)

Built from `HANDOFF_WEB_SEARCH.md` in six local commits (`bb79c8e` settings and public institutions, `cf4fe9b` search core, `54db361` pipeline and API, `9347aba` real-model planner check, `3f83318` web UI, then the review and docs commit). **Off by default** (`features.web_search: false`).

**What it does.** In a web chat with web search on, every message goes through a fixed pipeline (no tool loop): one planner call reads the lean masked conversation and returns up to N queries as JSON (`prompts/search_planner.md`; one retry, then a "Web search skipped … (Reason: …)" note); each query is checked locally (`web_search.check_query`: placeholders, currency amounts and by default dates removed; labelled Public, Generic or Skipped; fails closed); the app searches (Brave, ddgs or the fake engine), reads the top pages (SSRF guard on every hop, 10 s, 2 MB, text/html, trafilatura), masks the page text with `PrivacyEngine.mask_web_text` (token map + terms list, never the detectors), and answers with a delimited, untrusted `<web_sources>` block and `[n]` citations (`prompts/web_sources.md`). A real name reaches the search engine only through **Rerun with <Name>** (linked: the model then sees results about the placeholder) or **Rerun with name, hide from AI** (results shown to the user only; "Send these results to the model…" links it later). Currency amounts are never sent. Telegram, meetings and Adviser never search.

**Public institutions.** `public_institutions.py` (regulators, central banks, standard setters, exchanges, rating agencies) is applied where `place_names` is applied, in both detector modes, so these names are also no longer masked in chats. Acronyms match case-sensitively. The terms list wins. `institution_names: "mask_all"` restores the old behaviour. The privacy evaluation was unchanged by it (layered 95.24% recall; precision 75.03% with `mask_all` places, 86.98% with `common_public`): the invented test set contains no institution names, so it neither helped nor hurt there.

**Settings** (`settings.json`, Settings → Web search): `features.web_search`; `web_search.enabled_for_new_chats`, `engine` (brave | ddgs), `max_searches_per_message` (1–5, 3), `pages_per_search` (0–4, 2), `dates_in_queries` (remove | years), `terms_list_names` (rerun | never), `offer_hidden_rerun`, `show_link_banner`, `cost_per_search_usd` (0.005), `planner_model` (blank = chat model; no UI); `institution_names`, `public_institutions_added`, `public_institutions_removed`. The Brave key lives only in `.env` as `BRAVE_SEARCH_API_KEY`; Settings shows "key saved" or "no key". `PRIVACY_LLM_FAKE_SEARCH=1` switches every search to the offline fake engine (tests and scratch UI checks only).

**Endpoints and events.** `GET/PUT /api/web-search/settings`; `PATCH /api/sessions/{id}` accepts `web_search`; `POST /api/chat/stream` accepts `web_search` for a new chat; `POST /api/sessions/{id}/searches/{search_id}/rerun` with `{"hide_from_ai": bool}` and `POST /api/sessions/{id}/searches/{search_id}/send-to-model` (both NDJSON streams). New stream events: `planner_started`, `search_started`, `search_done`, `search_skipped`, `planner_failed`. The session list includes `has_linked_names`.

**Storage.** Optional session fields `web_search`, `searches`, `revealed_names`, `has_linked_names`; user messages of searched turns get an `id`. A rerun or send-to-model adds a compact system-generated user entry (`event: "rerun" | "send_to_model"`) so user/assistant alternation holds; it shows as a one-line event and cannot be edited. Server-only: `planner_query_masked`, `sent_with`, page `text`, `text_masked`, `label_masked`. Branching copies the searches of copied turns and their revealed names; edit-and-resend drops the removed turns' searches (revealed names stay, as a record of what left the machine). Older turns' sources are never re-sent: their messages carry `[web sources from an earlier message not included]`.

**Budget.** Before the planner, a `web_search` ledger entry (`model: web-search/<engine>`) reserves max searches × cost with headroom for the planner and the answer, and is settled to the searches actually run. Refusal shows "Search skipped: monthly limit reached" and the answer still goes ahead if it fits. A rerun reserves one search (+ the answer unless hidden).

**Real-model check.** `eval/run_search_planner_check.py --live` (8 invented masked cases, capped at US$0.05 through `dev_budget`): with the saved free model on 2026-11-25, 8/8 valid, none over the limit, no invented placeholders, $0 (and again 8/8 after the date was added to the prompt). The report also lists stale years per case. Re-run it after changing `prompts/search_planner.md` or the planner model.

**Switching it off.** Smallest first: untick Settings → Web search → "Turn on web search" (or per chat with the composer switch); set "Institution names" to "Masked like other names" (`institution_names: mask_all`); `git revert` the commits listed above. Python changes need a restart; prompts and `static/` do not.

**Known limits and follow-ups.** The SSRF guard resolves and checks every address before each hop, but the HTTP client resolves again when it connects (a DNS-rebinding window remains). The planner prompt now carries today's date and asks for "latest" instead of years; with the saved free model, stale years still appeared in 2 of 8 check cases (down from 5), usually as a hedge such as "2024 2025". When a planner-written year equals a year masked in that chat (a `DATE_TIME` value), the query check stops that search (fail closed), so such searches show as Failed. A rerun reserves the answer using a size estimate of the whole chat. Search results are not cached. `ddgs` is not installed by default (`pip install ddgs`). The optional live Brave call from section 10 has not been made (no key yet).

**Out of scope** (unchanged from `HANDOFF_WEB_SEARCH.md` section 12): Telegram and Adviser search, OpenRouter's built-in search, multi-step research or a tool loop, a local model for named results, search in finance features, caching, news/image/video search, the "Run evaluation" button.

---

## 13. Web search fixes (built 2026-11-25)

Built from `HANDOFF_WEB_SEARCH_FIXES.md` after the first real use, in local commits `cf2af2f` (step 1), `aeccffb` (step 2), `9c7416a` (step 3), `26c9d5a` (step 4) and the step 5 commit (checks, review, this section).

**Why search "did not run".** In the user's two examples web search was switched off (`features.web_search` was false in the running app). With it on, the first message of a new chat plans and searches; a regression test drives that path through `/api/chat/stream`. What was also wrong, and is fixed: the model was told to "never infer" placeholder values (so it called them placeholders and asked for names), it was never told what the search did, and public words were masked (a public figure, "this week", the topic "Digital Asset Custody").

**What changed**
- **Placeholder instruction** (`prompts/placeholders.md`, read on every request, no placeholder-shaped text) on every model call: web and Telegram chat (and Telegram titles), meeting analysis, titles and questions, the search planner, Adviser, rerun answers. It replaces "never infer their original values". Web chats also get a hint that "Send as written" exists.
- **Search status** for the model: a masked `<web_search_status>` block after the message for every outcome (ran, ran without <tokens>, skipped and what it needed, failed, budget, planner failure, none needed). When the feature is on but a chat's switch is off, the model is told to point the user to the switch. `web_sources.md`: never claim you cannot search when searches ran; point to "Rerun unmasked" when words were removed. The planner returns at least one query for an explicit search request. Users see "No web search needed for this message" when the planner plans none.
- **Dates are not masked** (`date_names: "pass"`, default). Only date-shaped findings pass: the privacy evaluation showed spaCy labels account numbers as DATE_TIME and can swallow a Chinese name into a date span, so those stay masked. The terms list still masks a listed date. The query check still removes leftover date placeholders from older chats; the "a real value appears in the query" check stays (it also protects names) but now accepts values that are public (dates while they pass, names treated as public).
- **Public vocabulary:** `public_topics.py` (198 generic regulatory and finance terms; `topic_names`, `public_topics_added`, `public_topics_removed`) and the user's `public_names` (starts empty), applied with places and institutions in both detector modes; the terms list wins; all in the fingerprint. Web text keeps public names and dates readable.
- **Rerun unmasked** replaces "Rerun with <Name>": the planner's query with every placeholder restored and currency amounts kept (ordinary searches still never send names or amounts). One bar per message runs every search of that message in one click with one new answer (`POST /api/sessions/{id}/turns/{turn}/rerun`, `hide_from_ai` as before; identical reruns are sent once; the per-search endpoint stays). Labels "Unmasked" and "Hidden from AI". `revealed_names` gain kinds `amount` and `date`. Stored kinds stay `named` / `named_hidden`.
- **Always treat <Name> as public** on the rerun bar (detector-found names only) and in "What was sent": adds to `public_names` (`POST /api/public-names`) and offers the rerun.
- **Send as written:** composer switch, off after each send; the typed text gets the terms list only (attachments are still masked); set only by the request's `send_as_written` field; kept by re-masking, edit-and-resend and branching; "Sent as written" chip and details section.
- **What was sent:** a message action opening "What the model received" (`GET /api/sessions/{id}/messages/{index}/sent`, on demand, from the stored masked text), with "Always treat as public" and "Add a missed name to your terms list" (`POST /api/terms`).
- **Settings:** a "Privacy vocabulary" card (Dates; Topic glossary with its editor; Your public names). `web_search.dates_in_queries` is retired (ignored if saved).

**Checks.** 315 offline tests pass. Privacy evaluation: recall unchanged (layered 95.24%, standard 77.25%); precision up (layered 75.03 -> 77.46% with `mask_all` places, 86.98 -> 90.27% with `common_public`; standard 72.21 -> 74.83% and 85.48 -> 89.19%). Real-model checks with the saved free model (US$0): the planner check passed 12/12 (both examples before and after the fixes; each explicit request got a query); the new `eval/run_placeholder_check.py` failed 2/6 on the first wording (replies explained that a word "is a privacy stand-in") and passed 6/6 after one change to `prompts/placeholders.md`.

**Rollback, smallest first:** `date_names: "mask"`; `topic_names: "mask_all"`; clear Your public names; leave "Send as written" off; switch web search off; `git revert` the commits above. Prompt files and `static/` need no restart; Python changes do.

**Known limits**
- The planner still sometimes adds stale years ("2024 2025") even with today's date in its prompt.
- Without search context, the model says it cannot browse the web (true for that chat); with search on, the status block tells it what ran.
- spaCy's kind can be wrong (an invented name came out as LOCATION); the instruction tells the model not to rely on it.
- "What was sent" shows the most recent masked copy of a message; after a fingerprint change (a new term) that is what the next request sent, not necessarily the first.
- The mockups in `docs/web_search_mockups/` have the new wording but still show one rerun area per card; the app shows one bar per message.
