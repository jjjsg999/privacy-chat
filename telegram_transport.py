"""Optional Telegram long polling adapter; uses the same engine and JSON sessions."""
import json
import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

from telegram import Update
from telegram.ext import Application, ApplicationHandlerStop, CommandHandler, MessageHandler, TypeHandler, filters

import engine
import storage

log = logging.getLogger(__name__)
HELP = (
    'New conversations default to meeting transcript capture. Use /new for normal chat. '
    'Privacy processing and the model are shared with Web Settings.\n\n'
    '/start — start a fresh meeting capture\n'
    '/help — show this help\n'
    '/terms — show global sensitive terms (edit in Web Settings)\n'
    '/title [text] — show or set the current title\n'
    '/new — archive this conversation and start fresh\n'
    '/meeting — start fresh transcript capture; analysis follows 5 seconds of silence\n'
    '/ask <question> — ask about the full analyzed meeting transcript'
)


def allowed_ids(value):
    parts = re.split(r'[,\s]+', value.strip()) if value.strip() else []
    if not parts or any(not p.isdecimal() or int(p) <= 0 for p in parts):
        raise ValueError('TELEGRAM_ALLOWED_USER_IDS must contain positive numeric user IDs, separated by commas or spaces.')
    return {int(p) for p in parts}


async def reply(update, text):
    # At most 4,000 UTF-16 units, including astral characters, per Telegram message.
    chunk, units = [], 0
    for character in text:
        size = 2 if ord(character) > 0xFFFF else 1
        if units + size > 4000:
            await update.effective_message.reply_text(''.join(chunk), parse_mode=None)
            chunk, units = [], 0
        chunk.append(character)
        units += size
    if chunk:
        await update.effective_message.reply_text(''.join(chunk), parse_mode=None)


async def keep_typing(update):
    """Show "typing…" until cancelled. Telegram clears it after about five seconds, so it is renewed every four."""
    while True:
        try:
            await update.effective_chat.send_action('typing')
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # only a courtesy indicator; never let it affect the analysis
        await _typing_pause(4)


_typing_pause = asyncio.sleep  # bound at import, so tests that stub asyncio.sleep cannot turn this into a busy loop


class TelegramTransport:
    def __init__(self, privacy, lock, authorized):
        self.privacy, self.lock, self.authorized = privacy, lock, authorized
        self.captures = {}
        self.chat_mode = set()
        self.tasks = set()

    async def guard(self, update, context):
        # The first handler runs before every command, message, or state lookup.
        user = update.effective_user
        if not user or user.id not in self.authorized:
            if update.effective_message:
                await reply(update, 'You are not authorized to use this bot.')
            raise ApplicationHandlerStop
        if not update.effective_chat or update.effective_chat.type != 'private':
            if update.effective_message:
                await reply(update, 'Please use a private chat with this bot.')
            raise ApplicationHandlerStop

    def active(self, update):
        path = storage.DATA / 'telegram_active.json'
        mapping = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        key = f'{update.effective_chat.id}:{update.effective_user.id}'
        session = None
        if mapping.get(key):
            try:
                candidate = storage.load_session(mapping[key])
                if (not candidate.get('archived_at')
                        and candidate.get('telegram_chat_id') == update.effective_chat.id
                        and candidate.get('telegram_user_id') == update.effective_user.id):
                    session = candidate
            except FileNotFoundError:
                pass
        return path, mapping, key, session

    async def help(self, update, context):
        await reply(update, HELP)

    def begin_capture(self, key, update):
        fresh = storage.new_session('telegram')
        fresh.update(session_type='meeting', mode='meeting_capture',
                     telegram_chat_id=update.effective_chat.id,
                     telegram_user_id=update.effective_user.id)
        self.captures[key] = {'session': fresh, 'chunks': [], 'task': None}

    async def terms(self, update, context):
        await reply(update, 'Global sensitive terms (edit in Web Settings):\n\n' +
                    (storage.settings()['sensitive_terms'].strip() or 'No custom terms configured.'))

    async def title(self, update, context):
        async with self.lock:
            _, _, _, session = self.active(update)
            if session is None:
                text = 'No active conversation. Send a message first.'
            elif context.args:
                title = ' '.join(context.args).strip()
                if len(title) > 100:
                    text = 'Please use a title of at most 100 characters.'
                else:
                    session.update(title=title, title_generated=True)
                    storage.save_session(session)
                    text = f'Title: {title}'
            else:
                text = f'Title: {session["title"]}'
        await reply(update, text)

    async def boundary(self, update, meeting=False):
        # Discard a pending capture BEFORE waiting for the lock. An analysis that is already running holds the lock for
        # as long as the model takes, so /new used to wait for it, let it finish and post it, and only then take effect.
        key = f'{update.effective_chat.id}:{update.effective_user.id}'
        capture = self.captures.pop(key, None)
        if capture and capture.get('task'):
            capture['task'].cancel()  # also aborts a running analysis; its lock is released as it unwinds
            await asyncio.gather(capture['task'], return_exceptions=True)
        async with self.lock:
            path, mapping, key, session = self.active(update)
            if session:
                session['archived_at'] = storage.now()
                storage.save_session(session)
            if key in mapping:
                del mapping[key]
                storage.write_json(path, mapping)
            # Empty capture stays in memory; never persist a blank session.
            if meeting:
                self.chat_mode.discard(key)
                self.begin_capture(key, update)
            else:
                self.chat_mode.add(key)

    async def new(self, update, context):
        await self.boundary(update)
        await reply(update, 'Ready for a fresh conversation. Send your first message; global sensitive terms are unchanged.')

    async def meeting(self, update, context):
        await self.boundary(update, meeting=True)
        await reply(update, 'Send the meeting transcript in one or more messages. I will analyze it after 5 seconds without another chunk. No /done is needed.')

    async def finish_capture(self, key, capture, update):
        typing = None
        try:
            await asyncio.sleep(5)
            if self.captures.get(key) is not capture:
                return
            # Masking a long transcript and the model's answer can take a minute or more; say so instead of going quiet.
            words = len(' '.join(capture['chunks']).split())
            await reply(update, f'Transcript received ({words:,} words). Analysing now; a long meeting can take a minute or two.')
            typing = asyncio.create_task(keep_typing(update))
            async with self.lock:
                if self.captures.get(key) is not capture:
                    return
                result = await engine.analyze_meeting(capture['session'], '\n\n'.join(capture['chunks']), self.privacy, title=False)
                path, mapping, _, _ = self.active(update)
                mapping[key] = result['id']
                storage.write_json(path, mapping)
                del self.captures[key]
            typing.cancel()
            await reply(update, result['intelligence'])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exception types are logged, never provider text. A ChatError's message is written to be safe to show.
            cause = type(exc.__cause__).__name__ if exc.__cause__ else '-'
            log.error('Meeting processing failed (%s, cause %s).', type(exc).__name__, cause)
            reason = f' Reason: {exc}' if isinstance(exc, engine.ChatError) else ''
            try:
                await reply(update, 'Meeting processing or delivery failed. Check Web for a saved result. If none exists, your chunks remain in memory; send another transcript chunk to retry, or /new to discard them.' + reason)
            except Exception:
                log.error('Could not deliver meeting error notice.')
            return
        finally:
            if typing:
                typing.cancel()
        # The title comes after the analysis has been delivered, so it no longer delays the reply. It is made from the
        # analysis (a small request); a failure keeps the placeholder title "Meeting intelligence".
        try:
            async with self.lock:
                await engine.title_meeting(storage.load_session(result['id']), self.privacy)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error('Meeting title failed (%s).', type(exc).__name__)

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.captures.clear()

    async def ask(self, update, context):
        if not context.args:
            await reply(update, 'Usage: /ask <question>')
            return
        async with self.lock:
            _, _, key, session = self.active(update)
            if key in self.captures or not session or session.get('session_type') != 'meeting':
                answer = 'Wait for a meeting analysis first. Use /meeting to capture a transcript.'
            else:
                try:
                    result = await engine.ask_meeting(session, ' '.join(context.args), self.privacy, channel='telegram')
                    answer = result['messages'][-1]['content']
                except engine.ChatError as exc:
                    answer = str(exc)
        await reply(update, answer)

    async def message(self, update, context):
        text = (update.effective_message.text or '').strip()
        if not text:
            return
        async with self.lock:
            path, mapping, key, session = self.active(update)
            if session is None and key not in self.captures and key not in self.chat_mode:
                self.begin_capture(key, update)
            if key in self.captures:
                capture = self.captures[key]
                capture['chunks'].append(text)
                if capture['task']:
                    capture['task'].cancel()
                task = asyncio.create_task(self.finish_capture(key, capture, update))
                capture['task'] = task
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
                return
            if session is None:
                session = storage.new_session('telegram')
                session.update(telegram_chat_id=update.effective_chat.id,
                               telegram_user_id=update.effective_user.id)
            try:
                result = await engine.chat(session, text, self.privacy, channel='telegram')
            except engine.ChatError as exc:
                answer = str(exc)
            else:
                mapping[key] = session['id']
                storage.write_json(path, mapping)
                answer = result['messages'][-1]['content']
        await reply(update, answer)

    async def unsupported(self, update, context):
        await reply(update, 'Please send a text message or use /help for commands.')

    async def error(self, update, context):
        # Do not log updates, provider responses, or token-bearing request URLs.
        log.error('Telegram update failed (%s).', type(context.error).__name__)
        if update and update.effective_message and update.effective_user and update.effective_user.id in self.authorized:
            try:
                await reply(update, 'Could not complete this request. Check the conversation in Web before retrying; a reply may already be saved.')
            except Exception:
                log.error('Could not deliver Telegram error notice.')

    def application(self, token):
        application = Application.builder().token(token).concurrent_updates(False).build()
        application.add_handler(TypeHandler(Update, self.guard), group=-1)
        for command, handler in [('start', self.meeting), ('help', self.help),
                ('terms', self.terms), ('title', self.title), ('new', self.new),
                ('meeting', self.meeting), ('ask', self.ask)]:
            application.add_handler(CommandHandler(command, handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.message))
        application.add_handler(MessageHandler(filters.ALL, self.unsupported))
        application.add_error_handler(self.error)
        return application


@asynccontextmanager
async def telegram_lifespan(privacy, lock):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        yield None
        return
    authorized = allowed_ids(os.environ.get('TELEGRAM_ALLOWED_USER_IDS', ''))
    # httpx INFO messages include Telegram's token-bearing URLs.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    transport = TelegramTransport(privacy, lock, authorized)
    application = transport.application(token)
    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(timeout=30, allowed_updates=['message'],
                                                drop_pending_updates=False, bootstrap_retries=0)
    except Exception:
        try:
            if application.updater.running:
                await application.updater.stop()
            if application.running:
                await application.stop()
            await application.shutdown()
        finally:
            raise RuntimeError('Telegram startup failed. Check the bot token and network connection.') from None
    try:
        yield application
    finally:
        try:
            await application.updater.stop()
        finally:
            try:
                await application.stop()
            finally:
                await transport.close()
                await application.shutdown()
