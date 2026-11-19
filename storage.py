"""Small, atomic JSON store; run one application worker."""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
TERMS = ROOT / 'sensitive_terms.txt'
CONFIG = ROOT / 'config.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2))


def settings():
    path = DATA / 'settings.json'
    result = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'model': os.environ.get('OPENROUTER_MODEL', 'openai/gpt-4o-mini')}
    result['sensitive_terms'] = TERMS.read_text(encoding='utf-8') if TERMS.exists() else ''
    result.setdefault('monthly_cost_ceiling_usd', '2.00')
    return result


def openrouter_api_key():
    """Read the local plaintext config key, falling back to the environment."""
    if CONFIG.exists():
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('config.json must contain a JSON object.')
        if 'openrouter_api_key' in config:
            return str(config['openrouter_api_key'] or '').strip()
    return os.environ.get('OPENROUTER_API_KEY', '').strip()


def save_openrouter_api_key(key):
    config = {}
    if CONFIG.exists():
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('config.json must contain a JSON object.')
    config['openrouter_api_key'] = key.strip()
    write_json(CONFIG, config)


def session_path(session_id):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', session_id):
        raise ValueError('Invalid session ID')
    return DATA / 'sessions' / f'{session_id}.json'


def load_session(session_id):
    return json.loads(session_path(session_id).read_text(encoding='utf-8'))


def save_session(session):
    session['updated_at'] = now()
    write_json(session_path(session['id']), session)


def delete_session(session_id):
    path = session_path(session_id)
    if not path.exists():
        raise FileNotFoundError(session_id)
    active_path = DATA / 'telegram_active.json'
    if active_path.exists():
        active = json.loads(active_path.read_text(encoding='utf-8'))
        remaining = {key: value for key, value in active.items() if value != session_id}
        if remaining != active:
            write_json(active_path, remaining)
    path.unlink()


def new_session(channel='web'):
    return dict(id=f'{channel}_{uuid4().hex}', title='New conversation', title_generated=False,
                created_at=now(), updated_at=now(), channel_origin=channel,
                session_type='chat', messages=[], token_map={})


def public_session(session):
    diagnosis = settings().get('chat_diagnosis', False)
    result = {k: v for k, v in session.items() if k not in ('token_map', 'meeting_api_request')}
    result['chat_diagnosis'] = diagnosis
    if diagnosis and 'meeting_api_request' in session:
        result['meeting_api_request'] = session['meeting_api_request']
    return result


def visible_session(session):
    if session.get('session_type') == 'meeting':
        return bool(session.get('transcript', '').strip() and session.get('intelligence', '').strip() and session.get('mode') == 'meeting_ready')
    return any(m.get('role') == 'user' and m.get('content', '').strip() for m in session.get('messages', []))


def list_sessions():
    result = []
    for path in (DATA / 'sessions').glob('*.json'):
        session = json.loads(path.read_text(encoding='utf-8'))
        if visible_session(session):
            result.append({k: session[k] for k in ('id', 'title', 'updated_at', 'channel_origin')})
    return sorted(result, key=lambda s: s['updated_at'], reverse=True)
