# Privacy LLM

A local FastAPI app with plain HTML/CSS/JavaScript, OpenRouter chat, and reversible Presidio + spaCy privacy processing. Includes Web chat, Settings, and an optional Telegram polling transport.

## Setup (PowerShell, Python 3.11 or 3.12)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m spacy download en_core_web_sm
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Use one worker. Settings accepts an OpenRouter model ID; the initial default is `openai/gpt-4o-mini`, overridable with `OPENROUTER_MODEL` before first saving Settings. The project-root `.env` file is loaded automatically at application startup, including when using the double-click launcher. Existing environment variables take precedence. Restart the app after editing `.env`. Missing NLP dependencies/model stop startup rather than disabling privacy.

After setup, Windows users can double-click **Start Privacy LLM.bat** to start the server and open the app in their browser. Keep its console window open while using the app; close it to stop the server. Add or change the OpenRouter key in Settings, or edit `config.json` in the project folder directly. It is a local plaintext file and is excluded from Git. `config.example.json` shows its format. The Settings page never sends the saved key back to the browser; leave its key field blank to keep the current key, or select Remove the saved key.

Settings includes a live model catalogue fetched by the backend, with name/provider-author search, context length, pricing in USD per million tokens, and available capabilities. Select a result or enter an ID, then Save settings. The saved model applies to normal chat, meeting analysis, titles and meeting Q&A. The public catalogue does not require an API key; its availability does not prove the configured key works.

The catalogue loads automatically. Filter by maximum input and output cost (USD per million tokens), input mode, output mode, and favourites. A zero price limit includes free token pricing; blank means unrestricted. Unknown prices are excluded when that price filter is active. Modes come from the live catalogue and describe model capabilities; the app still sends text. Prices shown are published token rates, not a quote for all modalities or tiers. Use Show more to access every matching result. Favourite stars save immediately to `data/settings.json`, independently of the model and sensitive-term edits; they survive restarts.

Connection status distinguishes an absent key from a configured but untested key. **Test connection** sends a short, capped request using the saved model and key; standard OpenRouter charges may apply. It reports success or a sanitized failure without returning the key or provider error body. A test result applies to that test only.

The Web conversation list refreshes every ten seconds while visible and when the window regains focus. Telegram/Web badges and timestamps identify each conversation. Meetings show one conversation history with the original transcript collapsed and the intelligence response below it. Opening or continuing a conversation retains its original session ID and context. Blank chats and incomplete meetings are excluded from the list and retrieval endpoint.

The app binds to loopback and has no login. Keep it local; do not expose it publicly. At narrow viewport widths, the conversation list is in the menu. Desktop Enter sends; Shift+Enter adds a line. Mobile uses the Send button.

## Files and privacy

- `app.py`: HTTP routes and static pages.
- `engine.py`: shared chat, OpenRouter calls, and stable titles generated after three user messages. Failed title requests retry on a later turn.
- `privacy_engine.py`: global terms first, then Presidio/spaCy; session-specific reversible tokens.
- `storage.py`: atomic JSON writes under `data/sessions/`, plus `data/settings.json`.
- `config.json`: local plaintext OpenRouter key, editable directly or through Settings. It is ignored by Git; copy the format from `config.example.json` if creating it manually.
- `telegram_transport.py`: authorization, commands, and long polling; `data/telegram_active.json` maps Telegram chats/users to shared session IDs.
- `sensitive_terms.txt`: editable in Settings or directly. One literal term per line, optionally `CLIENT: Example Company`, `SYSTEM: Internal Platform`, etc. Matching is case-insensitive at word boundaries (so `Ann` does not match `annual`); comments start with `#`.

All outbound conversation history is processed again on every turn so changed terms also protect older messages. Assistant output and generated titles are rehydrated locally. Empty chats are not saved. Conversations and token maps are stored as **unencrypted local files**; keep the workspace private. Detection is English-first and imperfect. Semantic replacements reduce exposure but do not guarantee anonymity. The sanitized content still goes to OpenRouter and its selected provider.

No database or Gmail processing is included. A single application lock serializes Web/Telegram chat and settings changes to keep file writes simple. Failed chat completions leave persisted conversation history unchanged.

## Telegram

Create a bot with Telegram's BotFather and save its token and your numeric user ID in `.env` in the project folder (this file is excluded from Git):

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
```

Launch normally using **Start Privacy LLM.bat** or the setup command. Alternatively, set environment variables in the same PowerShell window before starting the app:

```powershell
$env:TELEGRAM_BOT_TOKEN = "your-bot-token"
$env:TELEGRAM_ALLOWED_USER_IDS = "123456789,987654321"
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Use numeric Telegram **user IDs**, not usernames. With no bot token, Web runs alone. With a token, a missing or malformed allowlist fails startup. Unauthorized users are rejected before any session access or model call. Only private bot chats are supported, to avoid posting private answers or sensitive terms to groups.

Polling runs alongside FastAPI and stops with it. Use one process/worker per bot; no webhook or tunnel is needed. `/start` starts a fresh meeting capture (the same as `/meeting`); `/help` explains commands; `/terms` shows the global list (edit in Web Settings); `/title` shows the current title and `/title text` sets a stable manual title. With no active conversation, ordinary text starts meeting capture automatically. `/new` selects normal chat. Existing saved conversations resume after restart; an empty, unsent `/new` choice resets to the meeting default after restart. Normal chat uses the same privacy engine, selected model, history and automatic titles as Web.

`/new` archives the active session and waits for real content before saving another. Archived conversations remain accessible in Web. Global terms are preserved, while the new conversation gets a fresh token map. Active conversations survive restart and can be continued in Web using the same session ID. Long replies are split into Telegram-sized messages.

`/meeting` archives the current conversation and starts a fresh meeting capture with no prior history or token map. Send transcript chunks as ordinary text messages. Each chunk resets a five-second inactivity timer; after silence, the complete transcript is analyzed once, followed by a title request. No `/done` is required. The transcript, executive intelligence, title, and subsequent Q&A are saved in the same JSON session store. The meeting appears in Web only after successful analysis.

`/ask <question>` uses the full stored transcript, intelligence, and follow-up history. Ordinary messages after analysis (including Web continuations) also use meeting Q&A. Prompts are loaded at request time from `prompts/meeting_assistant.md` and `prompts/ask.md`; edits need no restart. Output is adaptive executive intelligence, not a fixed minutes template.

Unanalyzed capture is held in memory. Blank `/meeting` sessions are never persisted; `/new` or another `/meeting` discards pending capture and cancels its timer. Restarting the server loses pending capture. If analysis fails, chunks stay in memory and another transcript message retries after five seconds. Once analysis has started, the shared lock lets it finish before a new command changes the conversation boundary.

Telegram carries the original messages and rehydrated replies. Privacy substitution occurs locally before OpenRouter, not before Telegram. A Telegram delivery failure can occur after a reply was saved; check Web before resending.

## Smoke checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Checks use temporary storage and mocked Telegram/OpenRouter calls; no credentials or paid calls are needed. Telegram checks exercise its real handler dispatcher, authorization, commands, restart continuity, `/new`, reply splitting, and polling lifecycle. A separate local Presidio check can verify the downloaded model. Integration references: [Presidio Analyzer](https://microsoft.github.io/presidio/analyzer/), [OpenRouter REST quickstart](https://openrouter.ai/docs/quickstart), and [python-telegram-bot lifecycle](https://docs.python-telegram-bot.org/telegram.ext.application.html).

Assistant replies and meeting intelligence render Markdown headings, bold, lists, code blocks, and tables. Wide tables scroll horizontally on mobile. Original user messages and raw transcripts remain plain text. Marked and DOMPurify are bundled under `static/vendor` with licenses; rendering runs locally without CDN requests. HTML is sanitized with a limited tag allowlist; embedded images and scripts are not rendered.

Settings includes **Chat diagnosis to show text sent to LLM API** (off by default). Enable it before processing a meeting to save the exact initial analysis model and message text after privacy processing. Full transcript then compares original text with the recorded system and transcript messages. Title and follow-up requests do not replace that snapshot. Older meetings have no reconstructed snapshot. Turning diagnosis off hides snapshots from the UI/API and stops new capture, while existing snapshots remain in local session files.

Each sidebar conversation has a delete button with confirmation. Deletion permanently removes its local session file (including transcript, token map, and diagnosis snapshot) and clears any active Telegram mapping. It does not delete messages from Telegram.

## Monthly spending ceiling
Settings → Monthly cost ceiling defaults to US$2.00 per calendar month in Asia/Singapore. Save any nonnegative amount (0 pauses all model calls). Changes do not reset recorded usage. All shared OpenRouter completions—including web/Telegram, meeting analysis/Q&A/titles, advisers and connection tests—reserve a conservative maximum before transmission. Unknown prices or an insufficient balance block the request. Outputs are bounded at 6,000 tokens by default; callers with an explicit smaller bound retain it.

The private `data/monthly_budget/` ledger survives restarts and retains earlier months. Provider-reported cost replaces a reservation when available; failed calls and missing/invalid cost data retain it. Unexpected charges exceeding a reservation block further calls for reconciliation. Corrupt/missing initialized ledgers fail closed. No prompts, responses or credentials are written to the cost ledger.

The Settings usage display includes settled charges plus unresolved reservations, not necessarily the provider invoice total. Tracking begins with the first request after this feature is installed; past calls and spending outside this app are excluded. It does not change the OpenRouter account limit. The explicitly authorized development/history budget remains a separate additional guard; neither budget is reset by changing the monthly ceiling.

Provider price limits and cost reporting use OpenRouter's [provider-routing controls](https://openrouter.ai/docs/guides/routing/provider-selection) and [usage reporting](https://openrouter.ai/support). No paid calls are needed to test this feature.

## Current project handoff

Read [PROJECT_HANDOFF_CURRENT.md](PROJECT_HANDOFF_CURRENT.md) for the comprehensive current architecture, functionality, API/storage contracts, UX/UI, operations, validation and planning constraints. It includes the user's successful post-retirement Web and Telegram manual testing and supersedes older handoffs for current-state planning.

Memory is intentionally not implemented. Action Register, Project Register and My Role remain visible placeholders. See [MEMORY_FUTURE_INTERFACE_CONTRACT.md](MEMORY_FUTURE_INTERFACE_CONTRACT.md) and [MEMORY_RETIREMENT_FINAL_REPORT.md](MEMORY_RETIREMENT_FINAL_REPORT.md) for the retained boundary and dated retirement evidence. Older P1/Q0/Q1 documents describe historical work, not current functionality.
