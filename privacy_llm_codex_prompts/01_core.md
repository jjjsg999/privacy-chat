Read `AGENTS.md` and implement the core application.

Build only:
- FastAPI backend
- mobile-first responsive web chat
- Settings page
- OpenRouter integration
- Presidio + spaCy privacy pipeline
- global `sensitive_terms.txt`
- reversible session-scoped tokenization/rehydration
- JSON session persistence
- normal chat
- automatic chat title generation

Keep the implementation small.

Use a few clear modules such as:
- `app.py`
- `privacy_engine.py`
- config/settings module if needed
- templates/static
- prompts

Do not implement Telegram or meeting mode yet.

OpenRouter:
- read API key from environment
- use model selected from Settings

Privacy:
- global sensitive terms first
- Presidio second
- semantic reversible placeholders
- session-scoped mappings
- rehydrate assistant output

Web:
- mobile-first
- desktop compatible
- clean chat interface
- dedicated Settings page

Settings must include:
- global sensitive terms
- OpenRouter connection status
- currently selected model
- live OpenRouter model search/discovery
- model metadata/details when available
- connection/model test action

Keep the actual OpenRouter API key server-side. Do not expose the full secret in browser responses or HTML.

Persistence:
- JSON files
- no database

Title:
- generate once after approximately 3 user messages
- keep stable

Run only lightweight smoke checks and report the exact startup command.
