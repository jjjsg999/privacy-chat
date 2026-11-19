Read `AGENTS.md` and implement the Telegram transport.

Use:
- `python-telegram-bot`
- long polling
- no webhook
- no tunnel

Environment:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`

Reject unauthorized users before creating sessions or invoking the LLM.

Commands:
- `/start`
- `/help`
- `/terms`
- `/title`
- `/new`
- `/meeting`
- `/ask`

Normal messages use the shared normal-chat pipeline.

`/new`:
- archive current session
- start fresh session
- do not persist blank sessions

Do not duplicate privacy or OpenRouter logic inside Telegram.

Keep implementation small. Run only lightweight mock/smoke checks.
