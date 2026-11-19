import asyncio
from decimal import Decimal
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
    return response


@app.get('/')
async def index():
    return FileResponse(storage.ROOT / 'static' / 'index.html')


@app.get('/settings')
async def settings_page():
    return FileResponse(storage.ROOT / 'static' / 'settings.html')


@app.get('/memory')
async def memory_page():
    return FileResponse(storage.ROOT / 'static' / 'memory.html')


@app.get('/adviser')
async def adviser_page():
    return FileResponse(storage.ROOT / 'static' / 'adviser.html')


@app.get('/api/settings')
async def get_settings():
    config = storage.settings()
    return {'model': config['model'], 'sensitive_terms': config['sensitive_terms'],
            'favourites': config.get('favourites', []),
            'chat_diagnosis': config.get('chat_diagnosis', False),
            'monthly_cost_ceiling_usd': str(config.get('monthly_cost_ceiling_usd', '2.00')),
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
    monthly_cost_ceiling_usd: Decimal | None = Field(default=None, ge=0, le=100000, decimal_places=2)


@app.put('/api/settings')
async def put_settings(value: Settings):
    async with chat_lock:
        storage.atomic_write(storage.TERMS, value.sensitive_terms)
        config = storage.settings()
        config.pop('sensitive_terms', None)
        config.update(model=value.model, chat_diagnosis=value.chat_diagnosis)
        if value.monthly_cost_ceiling_usd is not None:
            config['monthly_cost_ceiling_usd'] = str(value.monthly_cost_ceiling_usd)
        storage.write_json(storage.DATA / 'settings.json', config)
        if value.clear_api_key:
            storage.save_openrouter_api_key('')
        elif value.openrouter_api_key.strip():
            storage.save_openrouter_api_key(value.openrouter_api_key)
    return await get_settings()


class Favourite(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=r'^\S+/\S+$')
    favourite: bool


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
async def sessions():
    return storage.list_sessions()


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


@app.post('/api/chat')
async def chat(value: Message):
    if not value.message.strip():
        raise HTTPException(422, 'Please enter a message.')
    async with chat_lock:
        current = read_session(value.session_id) if value.session_id else storage.new_session()
        try:
            return await engine.chat(current, value.message.strip(), app.state.privacy)
        except engine.ChatError as exc:
            raise HTTPException(502, str(exc))
