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
    # "standard" (today's detectors) or "layered" (terms > patterns > Privacy Filter > spaCy).
    result.setdefault('privacy_detectors', 'standard')
    # "common_public" lets countries, regions and major cities through; "mask_all" masks every place.
    result.setdefault('place_names', 'common_public')
    # How much history to send: lean | balanced | full (see context_builder.py).
    result.setdefault('context_mode_chat', 'balanced')
    result.setdefault('context_mode_meeting', 'full')
    # Spaces group chats for organising only. Ids are stable; renaming changes only the name.
    result['spaces'] = _clean_spaces(result.get('spaces'))
    result['telegram_space'] = telegram_space(result)
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


# ---------------------------------------------------------------- spaces

DEFAULT_SPACES = [{'id': 'work', 'name': 'Work'}, {'id': 'personal', 'name': 'Personal'}]
SPACE_NAME_MAX = 40


class SpaceError(ValueError):
    """A spaces rule was broken. `status` is the HTTP status the API uses."""

    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def _raw_settings():
    path = DATA / 'settings.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _clean_spaces(value):
    found, seen = [], set()
    for entry in value if isinstance(value, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get('id'), str) and entry['id'] and isinstance(entry.get('name'), str) \
                and entry['id'] not in seen:
            found.append({'id': entry['id'], 'name': entry['name']})
            seen.add(entry['id'])
    return found or [dict(s) for s in DEFAULT_SPACES]


def spaces(config=None):
    """The saved spaces, [{id, name}], in order. Falls back to Work and Personal."""
    return _clean_spaces((config if config is not None else _raw_settings()).get('spaces'))


def default_space(ids):
    return 'work' if 'work' in ids else ids[0]


def telegram_space(config=None):
    config = config if config is not None else _raw_settings()
    ids = [s['id'] for s in spaces(config)]
    return config.get('telegram_space') if config.get('telegram_space') in ids else default_space(ids)


def effective_space(session, ids=None):
    """The space a chat is in. A chat with no space, or one whose space no longer exists, is in Work."""
    ids = ids if ids is not None else [s['id'] for s in spaces()]
    return session.get('space') if session.get('space') in ids else default_space(ids)


def _save_config(config):
    config = dict(config)
    config.pop('sensitive_terms', None)
    if not (DATA / 'settings.json').exists():  # a first write must not leave a settings file with no model
        config.setdefault('model', os.environ.get('OPENROUTER_MODEL', 'openai/gpt-4o-mini'))
    write_json(DATA / 'settings.json', config)


def clean_space_name(name, existing, own_id=None):
    cleaned = str(name or '').strip()
    if not 1 <= len(cleaned) <= SPACE_NAME_MAX:
        raise SpaceError(f'Use a name of 1 to {SPACE_NAME_MAX} characters.')
    if any(s['name'].casefold() == cleaned.casefold() and s['id'] != own_id for s in existing):
        raise SpaceError('A space with that name already exists.', 409)
    return cleaned


def space_counts():
    """{space id: chats}, archived chats included. Chats with no or an unknown space count as Work."""
    ids = [s['id'] for s in spaces()]
    counts = {i: 0 for i in ids}
    folder = DATA / 'sessions'
    for path in folder.glob('*.json') if folder.exists() else []:
        session = json.loads(path.read_text(encoding='utf-8'))
        if visible_session(session):
            counts[effective_space(session, ids)] += 1
    return counts


def delete_blocker(space_id, listing=None, counts=None, config=None):
    """Why this space cannot be deleted, or None."""
    config = config if config is not None else _raw_settings()
    listing = listing if listing is not None else spaces(config)
    counts = counts if counts is not None else space_counts()
    if space_id not in {s['id'] for s in listing}:
        raise SpaceError('That space does not exist.', 404)
    if len(listing) <= 1:
        return 'This is the last space. Keep at least one.'
    if telegram_space(config) == space_id:
        return 'Telegram chats go to this space. Choose another Telegram space first.'
    if counts.get(space_id, 0):
        return f"It still has {counts[space_id]} chat{'s' if counts[space_id] != 1 else ''}, including archived ones. Move or delete them first."
    return None


def space_summary():
    config = _raw_settings()
    listing, counts = spaces(config), space_counts()
    return {'spaces': [{**s, 'chats': counts.get(s['id'], 0), 'delete_blocked': delete_blocker(s['id'], listing, counts, config)}
                       for s in listing], 'telegram_space': telegram_space(config)}


def add_space(name):
    config = _raw_settings()
    listing = spaces(config)
    config['spaces'] = listing + [{'id': f'sp_{uuid4().hex[:8]}', 'name': clean_space_name(name, listing)}]
    config['telegram_space'] = telegram_space(config)
    _save_config({**config, 'spaces': config['spaces']})
    return space_summary()


def rename_space(space_id, name):
    config = _raw_settings()
    listing = spaces(config)
    if space_id not in {s['id'] for s in listing}:
        raise SpaceError('That space does not exist.', 404)
    cleaned = clean_space_name(name, listing, space_id)
    config['spaces'] = [{**s, 'name': cleaned} if s['id'] == space_id else s for s in listing]  # the id never changes
    _save_config(config)
    return space_summary()


def delete_space(space_id):
    config = _raw_settings()
    listing = spaces(config)
    reason = delete_blocker(space_id, listing, None, config)
    if reason:
        raise SpaceError(reason, 409)
    config['spaces'] = [s for s in listing if s['id'] != space_id]
    _save_config(config)
    return space_summary()


def set_telegram_space(space_id):
    config = _raw_settings()
    if space_id not in {s['id'] for s in spaces(config)}:
        raise SpaceError('That space does not exist.', 404)
    config['telegram_space'] = space_id
    config['spaces'] = spaces(config)
    _save_config(config)
    return space_summary()


def set_session_space(session_id, space_id):
    """Move a chat to another space. Like rename, this keeps updated_at so list order is not disturbed."""
    if space_id not in {s['id'] for s in spaces()}:
        raise SpaceError('That space does not exist.')
    session = load_session(session_id)
    session['space'] = space_id
    write_json(session_path(session_id), session)
    return session


def session_path(session_id):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', session_id):
        raise ValueError('Invalid session ID')
    return DATA / 'sessions' / f'{session_id}.json'


def load_session(session_id):
    return json.loads(session_path(session_id).read_text(encoding='utf-8'))


def save_session(session):
    session['updated_at'] = now()
    session['space'] = effective_space(session)  # written the next time a session is saved, for any reason
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


def new_session(channel='web', space=None):
    """A new chat. Telegram chats and meetings go to the Telegram space; others to `space` (the default space if unknown)."""
    config = _raw_settings()  # never the terms file: creating a chat needs only the spaces
    ids = [s['id'] for s in spaces(config)]
    chosen = telegram_space(config) if channel == 'telegram' else (space if space in ids else default_space(ids))
    return dict(id=f'{channel}_{uuid4().hex}', title='New conversation', title_generated=False,
                created_at=now(), updated_at=now(), channel_origin=channel, space=chosen,
                session_type='chat', messages=[], token_map={})


CACHE_KEYS = ('masked', 'transcript_masked', 'intelligence_masked')  # masked-text caches stay on this machine


def public_session(session):
    diagnosis = settings().get('chat_diagnosis', False)
    result = {k: v for k, v in session.items() if k not in ('token_map', 'meeting_api_request') + CACHE_KEYS}
    if 'messages' in result:
        result['messages'] = [{k: v for k, v in m.items() if k not in CACHE_KEYS} for m in result['messages']]
    result['chat_diagnosis'] = diagnosis
    result['space'] = effective_space(session)
    if diagnosis and 'meeting_api_request' in session:
        result['meeting_api_request'] = session['meeting_api_request']
    return result


def visible_session(session):
    if session.get('session_type') == 'meeting':
        return bool(session.get('transcript', '').strip() and session.get('intelligence', '').strip() and session.get('mode') == 'meeting_ready')
    return any(m.get('role') == 'user' and m.get('content', '').strip() for m in session.get('messages', []))


def session_matches(session, needle):
    """Case-insensitive substring match over the title, messages and meeting transcript."""
    texts = [session.get('title', ''), session.get('transcript', '')]
    texts += [m.get('content', '') for m in session.get('messages', [])]
    return any(needle in str(text).casefold() for text in texts)


def rename_session(session_id, title):
    """Set a manual title that automatic titling will not replace; keeps updated_at (list order)."""
    session = load_session(session_id)
    session.update(title=title, title_generated=True)
    session['space'] = effective_space(session)
    write_json(session_path(session_id), session)
    return session


def set_session_flags(session_id, pinned=None, archived=None):
    """Pin or archive a conversation. Like rename, this keeps updated_at so list order is not disturbed."""
    session = load_session(session_id)
    if pinned is not None:
        session['pinned'] = bool(pinned)
    if archived is not None:
        session['archived'] = bool(archived)
        if session['archived']:
            session['pinned'] = False  # an archived conversation is out of the way, not pinned
    session['space'] = effective_space(session)
    write_json(session_path(session_id), session)
    return session


def set_pin(session_id, kind, pinned, index=None, file=None, artifact=None):
    """Keep-in-context pin on a message, a file chip or an artifact id. Like rename, this keeps updated_at."""
    session = load_session(session_id)
    messages = session.get('messages', [])
    if kind == 'artifact':
        ids = [a for a in session.get('pinned_artifacts', []) if a != artifact]
        session['pinned_artifacts'] = ids + [artifact] if pinned and artifact else ids
    else:
        if index is None or not 0 <= index < len(messages):
            raise ValueError('Choose one of the messages in this conversation.')
        target = messages[index]
        if kind == 'file':
            files = target.get('attachments') or []
            if file is None or not 0 <= file < len(files):
                raise ValueError('Choose one of the attached files.')
            target = files[file]
        elif kind != 'message':
            raise ValueError('Unknown pin type.')
        if pinned:
            target['pinned'] = True
        else:
            target.pop('pinned', None)
    session['space'] = effective_space(session)
    write_json(session_path(session_id), session)
    return session


def branch_session(session_id, message_index):
    """Copy a chat up to and including one assistant reply into a new web conversation.

    The copy gets its own token-map copy, so later messages in either conversation never affect the other.
    """
    source = load_session(session_id)
    messages = source.get('messages', [])
    if source.get('session_type', 'chat') != 'chat':
        raise ValueError('Only normal chats can be branched.')
    if not 0 <= message_index < len(messages) or messages[message_index].get('role') != 'assistant':
        raise ValueError('Branch from one of the assistant replies.')
    branch = new_session('web', effective_space(source))
    branch.update(title=(f"{source.get('title') or 'Conversation'} (branch)")[:100], title_generated=True,
                  messages=json.loads(json.dumps(messages[:message_index + 1])),
                  token_map=json.loads(json.dumps(source.get('token_map', {}))), branched_from=source['id'])
    if source.get('pinned_artifacts'):
        branch['pinned_artifacts'] = list(source['pinned_artifacts'])
    save_session(branch)
    return branch


def list_sessions(query=None, space=None):
    """Conversations, newest first. `space` is a space id, or None / 'all' for every space."""
    result = []
    needle = (query or '').strip().casefold()
    ids = [s['id'] for s in spaces()]
    for path in (DATA / 'sessions').glob('*.json'):
        session = json.loads(path.read_text(encoding='utf-8'))
        where = effective_space(session, ids)
        if space not in (None, '', 'all') and where != space:
            continue
        if visible_session(session) and (not needle or session_matches(session, needle)):
            item = {k: session[k] for k in ('id', 'title', 'updated_at', 'channel_origin')}
            item.update(pinned=bool(session.get('pinned')), archived=bool(session.get('archived')), space=where)
            result.append(item)
    return sorted(result, key=lambda s: s['updated_at'], reverse=True)
