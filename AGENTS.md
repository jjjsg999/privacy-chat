# Privacy LLM — Codex Project Contract

You are rebuilding a small personal privacy-preserving LLM application from scratch.

The objective is a simple, local-first personal intelligence system. Prefer straightforward Python and file-based persistence over frameworks and infrastructure.

## Product

The system has two primary interfaces:
- mobile-first responsive web UI
- Telegram

It also has an email ingestion workflow for meeting transcripts.

The core engine must be shared across all transports.

## Core architecture

Web UI ───────┐
Telegram ─────┼──> shared privacy/intelligence engine ──> OpenRouter
Gmail ────────┘

Transport adapters must not duplicate privacy or LLM logic.

## Technology

Backend:
- Python
- FastAPI

Frontend:
- plain HTML/CSS/JavaScript
- mobile-first responsive design
- desktop layout also supported
- do not introduce React unless genuinely necessary

Privacy:
- Microsoft Presidio
- spaCy
- automatic PII detection
- custom sensitive-term matching

LLM:
- OpenRouter REST API
- model selectable from Settings
- API key from environment

Persistence:
- simple JSON files / text files
- no database
- no Redis
- no vector database

Telegram:
- python-telegram-bot
- polling, not webhooks
- authorized Telegram user IDs from environment

Email:
- Gmail IMAP polling
- poll every 60 seconds
- SMTP reply to original sender

## Privacy model

Before sending content to an LLM:
1. Apply globally configured sensitive terms.
2. Apply Presidio PII detection.
3. Replace detected values with semantic reversible tokens.
4. Call the LLM.
5. Rehydrate response using the session token map.

Sensitive terms are global across Web and Telegram.

Store them in:

`sensitive_terms.txt`

The Settings UI edits the global list.

Use semantic tokens such as:
- `PERSON__1`
- `CLIENT__2`
- `SYSTEM__3`
- `REGULATOR__4`

Session token maps must be isolated.

## Session model

Sessions are persisted as JSON.

Session IDs must be transport-independent and globally unique.
Examples:
- `web_<id>`
- `telegram_<chat_id>`

A Telegram-created session can later be opened and continued from the Web UI using the SAME session.

Conversation state may include:
- messages
- token map
- title
- created_at
- updated_at
- channel_origin
- session_type
- Telegram identifiers
- meeting metadata

Global sensitive terms are NOT session state.

## Normal chat

Normal chat supports:
- conversation history
- OpenRouter model selection
- reversible privacy processing
- automatic title generation after approximately 3 user messages
- stable title once generated

Titles should be concise and useful.

## Telegram commands

Support:
- `/start`
- `/help`
- `/terms`
- `/title`
- `/new`
- `/meeting`
- `/ask`

Unauthorized Telegram users must be rejected before session creation or LLM processing.

## /new

`/new` archives the current active conversation and starts a fresh conversation.

Important:
- do not persist or show blank conversations
- if `/new` is followed by no real user content, do not store the new session
- preserve global sensitive terms
- reset conversation history and token map for the new session

## /meeting

`/meeting` must:
1. archive/create a fresh meeting conversation boundary
2. create a fresh meeting session with no prior chat context
3. switch the session into meeting transcript capture mode
4. wait for transcript messages
5. buffer multiple Telegram messages
6. do NOT call the LLM for each chunk
7. after 5 seconds of inactivity following transcript input, analyze the complete accumulated transcript
8. use the dedicated meeting prompt
9. store the full transcript and resulting analysis
10. allow later `/ask` against the full transcript

Do not require `/done`.

Do not create/persist a blank meeting session if no transcript is ever supplied.

## Meeting intelligence

The meeting feature is NOT primarily a meeting-minutes generator.

Primary objective:

Executive intelligence.

The model should understand:
1. what happened
2. why it matters
3. stakeholder positions
4. what leadership should pay attention to next

Also identify, when relevant:
- decisions
- implications
- risks
- conflicts
- alignment
- commitments
- action items
- unresolved issues
- likely next steps

Output should be adaptive, narrative, contextual, and executive-quality.

Do not force a rigid fixed template onto every meeting.

## Meeting prompt files

Keep meeting prompting simple.

Use:
- `prompts/meeting_assistant.md` as the main meeting system prompt
- `prompts/ask.md` for meeting Q&A

Do not create a multi-file meeting prompt hierarchy unless necessary.

Load prompt files from disk at runtime so the user can edit them without changing code.

## Meeting transcript packaging

Clearly tell the model that the input is a raw meeting transcript and may contain:
- interruptions
- speaker changes
- transcription errors
- incomplete sentences
- repetitions
- conversational filler

Tell the model to interpret the transcript rather than summarize it chronologically.

## /ask

`/ask <question>` on a meeting session must use the FULL stored transcript plus meeting context.

Do not answer using only the generated summary.

Do not invent facts.

If information is unavailable, say so.

## Chat titles

Meeting titles and normal conversation titles should be generated by the LLM where appropriate.

Titles should remain stable.

## Web UI

The web UI must be:
- mobile-first
- usable on desktop
- simple
- chat-oriented

Settings page contains at minimum:
- model selection
- global sensitive terms

Do not duplicate these controls unnecessarily inside the chat view.

## Cross-channel sessions

Telegram-created sessions must appear in the Web UI once they contain meaningful content.

Do not show blank `/new` or blank `/meeting` sessions.

Telegram-originated sessions display a Telegram channel badge.

Web-originated sessions display a Web badge.

Users can open Telegram-created sessions from the Web UI and continue chatting using the SAME session.

Meeting sessions opened on the Web UI should show:
- transcript
- summary / intelligence output
- action items where present
- conversation history

## Gmail meeting workflow

Gmail is an independent ingestion transport.

Poll every 60 seconds using IMAP.

Each unread email is treated as one meeting transcript.

For each email:
1. parse sender, subject, message ID, body
2. clean email body
3. process as a standalone meeting
4. generate executive meeting intelligence
5. reply to the ORIGINAL SENDER
6. preserve email threading headers where practical
7. mark the email processed

Use the same privacy protections before sending transcript material to OpenRouter.

Do not share state with Telegram conversations.

## Gmail configuration

Use environment variables for credentials.

Do not hardcode secrets.

## Simplicity constraints

Do NOT add:
- Docker
- Kubernetes
- Redis
- PostgreSQL
- vector databases
- LangChain
- LiteLLM
- Celery
- Kafka
- complex dependency injection
- microservices
- unnecessary abstractions

Prefer a small number of understandable Python modules.

## Testing philosophy

Do not build a large test suite.

Only test the critical paths:
- privacy tokenization/rehydration
- Telegram authorization
- Telegram /new
- Telegram /meeting transcript buffering
- 5-second meeting trigger
- meeting /ask
- cross-channel session retrieval
- Gmail ingest/reply

Use lightweight smoke tests and targeted unit tests.

## Definition of done

The application should be runnable locally with a straightforward startup command and should provide:
- mobile-first web chat
- settings
- OpenRouter chat
- privacy protection
- Telegram chat
- Telegram meeting mode
- meeting Q&A
- Gmail transcript processing
- cross-channel session visibility
- persistent file-based sessions

Keep implementation economical in both code and agent context.
Do not add features not specified above.
