Read `AGENTS.md` and implement the cross-channel UI and Settings enhancements.

Telegram-created sessions must appear in the Web UI after meaningful content exists.

Do not show blank `/new` sessions.
Do not show blank `/meeting` sessions.

For `/new`:
- temporary title may exist in memory
- persist/show only after real user content

For `/meeting`:
- persist/show only after transcript exists and analysis is complete

## Web chat list

- show title
- show Telegram/Web channel badge
- show timestamps where useful

Opening a Telegram-created session from Web must show the SAME session.
The user must be able to continue chatting from Web using the same history/context.

Meeting sessions should expose:
- transcript
- meeting intelligence
- action items where present
- full history

## Mobile-first UI

Preserve the existing mobile-first UI and desktop layout.
Keep Settings separate from chat.

## Settings: Privacy

Keep:
- global sensitive terms

## Settings: LLM / OpenRouter

Create a proper LLM configuration section.

Requirements:

1. OpenRouter connection status
   - clearly show whether an OpenRouter API key is configured and usable
   - do not display the full API key
   - do not return the secret to browser JavaScript

2. Current model
   - show the currently selected OpenRouter model ID/name
   - allow the user to change it

3. Model search / discovery
   - provide a search box for OpenRouter models
   - retrieve the live OpenRouter model catalogue through the backend, not directly from browser code using the secret
   - allow filtering/search by model name and provider when data is available

4. Model details
   - show useful metadata returned by OpenRouter, such as model name, provider, context length, pricing, and other available capabilities
   - gracefully omit unavailable fields

5. Select model
   - allow the user to select a model
   - persist the selected model in the existing local configuration mechanism
   - use the selected model for subsequent normal chat and meeting operations

6. Test connection
   - provide a small "Test connection" action
   - test the currently configured OpenRouter credentials/model through the backend
   - return a simple success/failure message
   - do not expose secrets or raw credential errors to the browser

## API / backend behavior

Add only the minimum endpoints needed, for example:

- GET settings/config
- GET OpenRouter model catalogue/search
- POST/select model
- POST/test OpenRouter connection

Reuse existing configuration and OpenRouter code where possible.

Do not create a second configuration store.

Do not add a database.

Do not expose the OpenRouter API key to the frontend.

## Security

The browser must never receive the full OpenRouter API key.

Prefer showing:
- Connected / Not connected
- optionally a masked suffix if already supported safely

If the existing application already has environment-only key management, preserve it.

Do not build key export functionality.

## Compatibility

Existing:
- web chat
- Telegram
- meeting mode
- session storage
- privacy controls

must continue working.

Do not introduce a second session database.

Run only a few critical smoke checks:
- Settings loads
- model search returns results when credentials are configured
- selected model persists
- test connection works/fails gracefully
- Telegram-created sessions still appear in Web
- Web can continue Telegram-created sessions
- blank sessions remain hidden
- mobile-first Web UI still works
