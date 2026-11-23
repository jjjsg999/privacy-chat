import asyncio
import json
from decimal import Decimal
from contextlib import aclosing, asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import documents
import engine
import adviser
import storage
import monthly_budget
from memory_api import router as memory_router
from adviser_api import router as adviser_router
from privacy_engine import PrivacyEngine
from telegram_transport import telegram_lifespan


@asynccontextmanager
async def lifespan(app):
    # Resolve against the project, even when launched from another directory.
    # Explicit shell environment variables take precedence over the file.
    load_dotenv(storage.ROOT / '.env', override=False, encoding='utf-8-sig')
    app.state.privacy = await asyncio.to_thread(PrivacyEngine)
    async with telegram_lifespan(app.state.privacy, chat_lock):
        yield


app = FastAPI(title='Privacy LLM', lifespan=lifespan)
app.include_router(memory_router)
app.include_router(adviser_router)
app.mount('/static', StaticFiles(directory=storage.ROOT / 'static'), name='static')
# One lock keeps disk updates ordered without a database or lock registry.
chat_lock = asyncio.Lock()


@app.middleware('http')
async def local_browser_guard(request: Request, call_next):
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.headers.get('host'):
        return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if request.url.path != '/artifact-frame':  # that page sets its own, deliberately different policy
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'self'"
    return response


@app.get('/')
async def index():
    return FileResponse(storage.ROOT / 'static' / 'index.html')


# The sandboxed page that shows artifacts. The browser runs it with an opaque origin (iframe sandbox plus the CSP
# `sandbox` directive): it can run the artifact's inline scripts but has no network, no access to this app's pages,
# API or storage, and cannot open windows or submit forms. The artifact text is posted in by the parent page.
ARTIFACT_FRAME = (
    '<!doctype html><html><head><meta charset="utf-8"><title>Artifact</title><script>'
    "addEventListener('message',function(e){var d=e.data;if(e.source!==parent||!d||d.type!=='artifact'||typeof d.html!=='string')return;"
    'document.open();document.write(d.html);document.close();});'
    "parent.postMessage({type:'artifact-ready'},'*');"
    '</script></head><body></body></html>')
ARTIFACT_FRAME_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                      "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; "
                      "form-action 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; "
                      "frame-ancestors 'self'; sandbox allow-scripts")


@app.get('/artifact-frame')
async def artifact_frame():
    return HTMLResponse(ARTIFACT_FRAME, headers={'Content-Security-Policy': ARTIFACT_FRAME_CSP})


@app.get('/settings')
async def settings_page():
    return FileResponse(storage.ROOT / 'static' / 'settings.html')


@app.get('/models')
async def models_page():
    return FileResponse(storage.ROOT / 'static' / 'models.html')


@app.get('/memory')
async def memory_page():
    return FileResponse(storage.ROOT / 'static' / 'memory.html')


@app.get('/adviser')
async def adviser_page():
    return FileResponse(storage.ROOT / 'static' / 'adviser.html')


@app.get('/api/settings')
async def get_settings():
    config = storage.settings()
    return {'model': config['model'], 'meeting_model': str(config.get('meeting_model') or ''),
            'sensitive_terms': config['sensitive_terms'],
            'favourites': config.get('favourites', []),
            'chat_diagnosis': config.get('chat_diagnosis', False),
            'review_extracted_text': bool(config.get('review_extracted_text', False)),
            'web_instructions': config.get('web_instructions', ''),
            'telegram_instructions': config.get('telegram_instructions', ''),
            'monthly_cost_ceiling_usd': str(config.get('monthly_cost_ceiling_usd', '2.00')),
            'context_mode_chat': engine.context_mode(config, False),
            'context_mode_meeting': engine.context_mode(config, True),
            'spaces': storage.spaces(config), 'telegram_space': storage.telegram_space(config),
            'monthly_budget': monthly_budget.status(),
            'openrouter': {'configured': bool(storage.openrouter_api_key())}}


@app.get('/api/openrouter/models')
async def models():
    try:
        return await engine.model_catalogue()
    except engine.ChatError as exc:
        raise HTTPException(502, str(exc))


@app.post('/api/openrouter/test')
async def test_connection():
    model = storage.settings()['model']
    try:
        await engine.complete([{'role': 'user', 'content': 'Reply with OK.'}], model, max_tokens=64)
        return {'ok': True, 'model': model, 'message': 'Connected. The saved model responded successfully.'}
    except engine.CostLimitError as exc:
        return {'ok': False, 'model': model, 'message': str(exc)}
    except engine.ChatError:
        return {'ok': False, 'model': model, 'message': 'Not connected. Check the server API key, selected model, credits and network, then retry.'}


class Settings(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=r'^\S+/\S+$')
    sensitive_terms: str = Field(max_length=100_000)
    openrouter_api_key: str = Field(default='', max_length=1000)
    clear_api_key: bool = False
    chat_diagnosis: bool = False
    meeting_model: str | None = Field(default=None, max_length=200, pattern=r'^(\S+/\S+)?$')  # '' = use the main model
    review_extracted_text: bool | None = None
    web_instructions: str | None = Field(default=None, max_length=2000)
    telegram_instructions: str | None = Field(default=None, max_length=2000)
    monthly_cost_ceiling_usd: Decimal | None = Field(default=None, ge=0, le=100000, decimal_places=2)
    context_mode_chat: Literal['lean', 'balanced', 'full'] | None = None
    context_mode_meeting: Literal['lean', 'balanced', 'full'] | None = None


@app.put('/api/settings')
async def put_settings(value: Settings):
    async with chat_lock:
        storage.atomic_write(storage.TERMS, value.sensitive_terms)
        config = storage.settings()
        config.pop('sensitive_terms', None)
        config.update(model=value.model, chat_diagnosis=value.chat_diagnosis)
        if value.meeting_model is not None:
            config['meeting_model'] = value.meeting_model
        if value.review_extracted_text is not None:
            config['review_extracted_text'] = value.review_extracted_text
        for name in ('web_instructions', 'telegram_instructions'):
            if getattr(value, name) is not None:  # omitted = keep the saved text
                config[name] = getattr(value, name).strip()
        if value.monthly_cost_ceiling_usd is not None:
            config['monthly_cost_ceiling_usd'] = str(value.monthly_cost_ceiling_usd)
        for name in ('context_mode_chat', 'context_mode_meeting'):
            if getattr(value, name) is not None:
                config[name] = getattr(value, name)
        storage.write_json(storage.DATA / 'settings.json', config)
        if value.clear_api_key:
            storage.save_openrouter_api_key('')
        elif value.openrouter_api_key.strip():
            storage.save_openrouter_api_key(value.openrouter_api_key)
    return await get_settings()


class Favourite(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=r'^\S+/\S+$')
    favourite: bool


class ModelChoice(BaseModel):
    model: str = Field(max_length=200, pattern=r'^(\S+/\S+)?$')
    purpose: Literal['general', 'meeting'] = 'general'


@app.put('/api/settings/model')
async def choose_model(value: ModelChoice):
    """Change only one model: 'general' (all other chats) or 'meeting' (blank = follow the general model)."""
    if value.purpose == 'general' and not value.model:
        raise HTTPException(422, 'Choose a model.')
    async with chat_lock:
        config = storage.settings()
        config.pop('sensitive_terms', None)
        config['model' if value.purpose == 'general' else 'meeting_model'] = value.model
        storage.write_json(storage.DATA / 'settings.json', config)
    return {'model': config['model'], 'meeting_model': str(config.get('meeting_model') or '')}


@app.put('/api/settings/favourite')
async def favourite(value: Favourite):
    async with chat_lock:
        config = storage.settings()
        favourites = set(config.get('favourites', []))
        if value.favourite:
            favourites.add(value.model)
        else:
            favourites.discard(value.model)
        config.pop('sensitive_terms', None)
        config['favourites'] = sorted(favourites)
        storage.write_json(storage.DATA / 'settings.json', config)
    return {'favourites': sorted(favourites)}


@app.get('/api/sessions')
async def sessions(q: str | None = Query(default=None, max_length=200), space: str | None = Query(default=None, max_length=100)):
    """`space` is a space id or 'all'; leave it out for every space."""
    if space not in (None, '', 'all') and space not in {s['id'] for s in storage.spaces()}:
        raise HTTPException(422, 'Unknown space.')
    return storage.list_sessions(q, space)


class SpaceName(BaseModel):
    name: str = Field(max_length=200)


class TelegramSpace(BaseModel):
    space: str = Field(min_length=1, max_length=100)


def space_call(action, *args):
    try:
        return action(*args)
    except storage.SpaceError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except OSError:
        raise HTTPException(500, 'Could not save the spaces.') from None


@app.get('/api/spaces')
async def list_spaces():
    return storage.space_summary()


@app.post('/api/spaces')
async def add_space(value: SpaceName):
    async with chat_lock:
        return space_call(storage.add_space, value.name)


@app.put('/api/spaces/telegram')
async def telegram_space(value: TelegramSpace):
    async with chat_lock:
        return space_call(storage.set_telegram_space, value.space)


@app.patch('/api/spaces/{space_id}')
async def rename_space(space_id: str, value: SpaceName):
    async with chat_lock:
        return space_call(storage.rename_space, space_id, value.name)


@app.delete('/api/spaces/{space_id}')
async def delete_space(space_id: str):
    async with chat_lock:
        return space_call(storage.delete_space, space_id)


class PinChange(BaseModel):
    """Keep-in-context pin: a message (index), a file chip (index + file) or an artifact id (its latest version)."""
    kind: Literal['message', 'file', 'artifact']
    pinned: bool
    index: int | None = Field(default=None, ge=0)
    file: int | None = Field(default=None, ge=0)
    artifact: str | None = Field(default=None, min_length=1, max_length=100)


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    pinned: bool | None = None
    archived: bool | None = None
    pin: PinChange | None = None
    space: str | None = Field(default=None, min_length=1, max_length=100)


@app.patch('/api/sessions/{session_id}')
async def update_session(session_id: str, value: SessionUpdate):
    """Rename, pin or archive. Fields that are omitted are left unchanged."""
    if value.title is None and value.pinned is None and value.archived is None and value.pin is None and value.space is None:
        raise HTTPException(422, 'Nothing to change.')
    title = ' '.join(value.title.split()) if value.title is not None else None
    if value.title is not None and not title:
        raise HTTPException(422, 'Please enter a title.')
    async with chat_lock:
        read_session(session_id)
        result = None
        if title:
            result = storage.rename_session(session_id, title)
        if value.pinned is not None or value.archived is not None:
            result = storage.set_session_flags(session_id, value.pinned, value.archived)
        if value.space is not None:
            try:
                result = storage.set_session_space(session_id, value.space)
            except storage.SpaceError as exc:
                raise HTTPException(422, str(exc)) from None
        if value.pin is not None:
            if value.pin.kind == 'artifact' and not value.pin.artifact:
                raise HTTPException(422, 'Choose an artifact to pin.')
            try:
                result = storage.set_pin(session_id, value.pin.kind, value.pin.pinned, value.pin.index, value.pin.file, value.pin.artifact)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        return storage.public_session(result)


class Branch(BaseModel):
    message_index: int = Field(ge=0)


@app.post('/api/sessions/{session_id}/branch')
async def branch_session(session_id: str, value: Branch):
    """Start a new conversation from a copy of this chat up to one assistant reply."""
    async with chat_lock:
        read_session(session_id)
        try:
            return storage.public_session(storage.branch_session(session_id, value.message_index))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError:
            raise HTTPException(500, 'Could not save the new conversation.') from None


@app.get('/api/spend/models')
async def spend_by_model():
    return monthly_budget.spend_by_model()


def read_session(session_id):
    try:
        result = storage.load_session(session_id)
        if not storage.visible_session(result):
            raise FileNotFoundError
        return result
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, 'Conversation not found.')


@app.get('/api/sessions/{session_id}')
async def session(session_id: str):
    return storage.public_session(read_session(session_id))


@app.delete('/api/sessions/{session_id}')
async def delete_session(session_id: str):
    async with chat_lock:
        read_session(session_id)
        storage.delete_session(session_id)
    return {'deleted': session_id}


class Message(BaseModel):
    session_id: str | None = Field(default=None, max_length=100)
    message: str = Field(min_length=1, max_length=50_000)
    space: str | None = Field(default=None, max_length=100)  # for a new chat; an unknown id falls back to Work


class AdviserMessage(BaseModel):
    persona_id: str = Field(min_length=1, max_length=100)
    session_id: str | None = Field(default=None, max_length=100)
    message: str = Field(min_length=1, max_length=20_000)
    expected_revision: int = Field(default=0, ge=0)


@app.get('/api/adviser/sessions')
async def adviser_sessions():
    try:
        return adviser.list_sessions()
    except ValueError:
        raise HTTPException(503, 'Adviser conversations unavailable.') from None


@app.get('/api/adviser/sessions/{identifier}')
async def adviser_session(identifier: str):
    try:
        return adviser.public_session(adviser.load_session(identifier))
    except ValueError:
        raise HTTPException(404, 'Adviser conversation unavailable.') from None


@app.post('/api/adviser/chat')
async def adviser_chat(value: AdviserMessage):
    async with chat_lock:
        try:
            return await adviser.chat(value.persona_id, value.message, app.state.privacy,
                session_id=value.session_id, expected_revision=value.expected_revision, purpose=None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except engine.ChatError as exc:
            raise HTTPException(502, str(exc)) from None
        except OSError:
            raise HTTPException(503, 'Could not save adviser conversation.') from None


class AttachmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern='^(exact|ocr)$')
    text: str = Field(min_length=1, max_length=1_000_000)  # real caps come from documents.limits()


class StreamMessage(BaseModel):
    session_id: str | None = Field(default=None, max_length=100)
    message: str = Field(default='', max_length=50_000)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=100)
    space: str | None = Field(default=None, max_length=100)  # for a new chat; an unknown id falls back to Work
    edit_index: int | None = Field(default=None, ge=0)  # replace this user message (and everything after it)


def check_attachment_limits(files):
    lim = documents.limits()
    if len(files) > lim['max_attachments']:
        raise HTTPException(422, f'Attach at most {lim["max_attachments"]} files per message.')
    if sum(len(f['text']) for f in files) > lim['max_total_chars']:
        raise HTTPException(422, f'Attachments exceed {lim["max_total_chars"]:,} characters in total.')
    for f in files:
        if len(f['text']) > lim['max_chars_per_attachment']:
            raise HTTPException(422, f'{f["name"]}: more than {lim["max_chars_per_attachment"]:,} characters.')


def apply_edit(session, index, typed):
    """Drop the chosen user message and everything after it, in memory only; return its replacement.

    Nothing is written until the new reply streams and is saved, so a failed edit leaves the saved chat intact.
    Attached file text is kept; only the typed part can change.
    """
    if session.get('session_type', 'chat') != 'chat':
        raise HTTPException(422, 'Only normal chats can be edited.')
    messages = session['messages']
    if index >= len(messages) or messages[index].get('role') != 'user':
        raise HTTPException(422, 'Choose one of your own messages to edit.')
    old = messages[index]
    meta = old.get('attachments') or []
    rest = ''
    if meta:
        old_typed = old.get('typed', '')
        if not old['content'].startswith(old_typed):
            raise HTTPException(422, 'This message cannot be edited.')
        rest = old['content'][len(old_typed):].strip()
    message = '\n\n'.join(part for part in (typed, rest) if part)
    if not message:
        raise HTTPException(422, 'Please enter a message.')
    session['messages'] = messages[:index]
    return message, ({'typed': typed, 'attachments': meta} if meta else None)


@app.post('/api/upload')
async def upload(files: list[UploadFile] = File(...)):
    """Extract text locally (OCR for images and scans). Nothing is stored; the browser keeps the result."""
    lim = documents.limits()
    if len(files) > lim['max_attachments']:
        raise HTTPException(422, f'Attach at most {lim["max_attachments"]} files per message.')
    review = bool(storage.settings().get('review_extracted_text', False))
    extracted = []
    for upload_file in files:
        data = await upload_file.read(lim['max_bytes'] + 1)
        try:
            item = await asyncio.to_thread(documents.extract, upload_file.filename, data, lim)
        except documents.DocumentError as exc:
            raise HTTPException(422, str(exc)) from None
        item['review'] = review and item['source'] in ('pdf', 'image')
        extracted.append(item)
    check_attachment_limits(extracted)
    return {'files': extracted, 'limits': {k: lim[k] for k in ('max_attachments', 'max_bytes', 'max_pdf_pages')}}


@app.post('/api/chat/stream')
async def chat_stream(value: StreamMessage):
    """Line-delimited JSON events: delta, done, title, error. Holds chat_lock for the whole stream."""
    typed = value.message.strip()
    files = [f.model_dump() for f in value.attachments]
    editing = value.edit_index is not None
    if editing and (files or not value.session_id):
        raise HTTPException(422, 'An edit needs an existing conversation and cannot change attachments.')
    if not typed and not files and not editing:
        raise HTTPException(422, 'Please enter a message.')
    check_attachment_limits(files)
    message = documents.compose_message(typed, files)
    extra = {'typed': typed, 'attachments': [{'name': f['name'], 'kind': f['kind'], 'chars': len(f['text'])} for f in files]} if files else None

    def line(event):
        return json.dumps(event, ensure_ascii=False) + '\n'

    async def events():
        nonlocal message, extra
        async with chat_lock:
            try:
                current = read_session(value.session_id) if value.session_id else storage.new_session(space=value.space)
                if editing:
                    message, extra = apply_edit(current, value.edit_index, typed)
            except HTTPException as exc:
                yield line({'type': 'error', 'message': exc.detail, 'saved': False})
                return
            try:
                async with aclosing(engine.stream_chat(current, message, app.state.privacy, extra)) as stream:
                    async for event in stream:
                        yield line(event)
            except engine.ChatError as exc:
                yield line({'type': 'error', 'message': str(exc), 'saved': False})
            except OSError:
                yield line({'type': 'error', 'message': 'Could not save the conversation.', 'saved': False})

    return StreamingResponse(events(), media_type='application/x-ndjson')


@app.post('/api/chat')
async def chat(value: Message):
    if not value.message.strip():
        raise HTTPException(422, 'Please enter a message.')
    async with chat_lock:
        current = read_session(value.session_id) if value.session_id else storage.new_session(space=value.space)
        try:
            return await engine.chat(current, value.message.strip(), app.state.privacy)
        except engine.ChatError as exc:
            raise HTTPException(502, str(exc))
