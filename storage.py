"""Small, atomic JSON store; run one application worker."""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import meeting_record

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


CACHE_KEYS = ('masked', 'transcript_masked', 'intelligence_masked')  # masked-text caches stay on this machine


def public_session(session):
    diagnosis = settings().get('chat_diagnosis', False)
    result = {k: v for k, v in session.items() if k not in ('token_map', 'meeting_api_request') + CACHE_KEYS}
    if 'messages' in result:
        result['messages'] = [{k: v for k, v in m.items() if k not in CACHE_KEYS} for m in result['messages']]
    if result.get('session_type') == 'meeting' and result.get('transcript'):
        result['transcript_lines'] = meeting_record.clean_lines(result['transcript'])  # what the L-numbers refer to
    result['chat_diagnosis'] = diagnosis
    if diagnosis and 'meeting_api_request' in session:
        result['meeting_api_request'] = session['meeting_api_request']
    return result


def visible_session(session):
    if session.get('session_type') == 'meeting':
        return bool(session.get('transcript', '').strip() and meeting_record.brief_of(session.get('intelligence')).strip()
                and session.get('mode') == 'meeting_ready')
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
    branch = new_session('web')
    branch.update(title=(f"{source.get('title') or 'Conversation'} (branch)")[:100], title_generated=True,
                  messages=json.loads(json.dumps(messages[:message_index + 1])),
                  token_map=json.loads(json.dumps(source.get('token_map', {}))), branched_from=source['id'])
    if source.get('pinned_artifacts'):
        branch['pinned_artifacts'] = list(source['pinned_artifacts'])
    save_session(branch)
    return branch


ACTION_STATUSES = ('open', 'done')


def _action_rows(session):
    record = session.get('intelligence')
    if session.get('session_type') != 'meeting' or not isinstance(record, dict):
        return []
    state = session.get('action_state') or {}
    try:
        reference = datetime.fromisoformat(session.get('created_at')).date()
    except (TypeError, ValueError):
        reference = datetime.now(timezone.utc).date()
    rows = []
    for action in record.get('actions') or []:
        current = state.get(action['id']) or {}
        due = meeting_record.parse_due(action.get('due'), reference)
        rows.append({'session_id': session['id'], 'meeting_title': session.get('title') or 'Meeting',
                     'meeting_date': session.get('created_at'), 'archived': bool(session.get('archived')),
                     'action_id': action['id'], 'task': action['task'], 'owner': action.get('owner'),
                     'due': action.get('due'), 'due_sort': due.isoformat() if due else None, 'lines': action.get('lines', []),
                     'verified': action.get('verified', True), 'reason': action.get('reason'),
                     'status': current.get('status', 'open'), 'updated_at': current.get('updated_at')})
    return rows


def list_actions(status='open', owner=None, meeting=None, include_archived=False):
    """Actions across all meeting sessions, for the tracker. Status is kept apart from the model's record.

    Sorted by due date where it parses (common formats), then the rest in the order they were stated, newest meeting first.
    """
    rows, meetings = [], {}
    for path in (DATA / 'sessions').glob('*.json'):
        session = json.loads(path.read_text(encoding='utf-8'))
        if not visible_session(session) or (session.get('archived') and not include_archived):
            continue
        found = _action_rows(session)
        if found:
            meetings[session['id']] = {'id': session['id'], 'title': session.get('title') or 'Meeting', 'date': session.get('created_at')}
        rows += found
    owners = sorted({r['owner'] for r in rows if r['owner']}, key=str.casefold)
    needle = (owner or '').strip().casefold()
    rows = [r for r in rows if (status in (None, '', 'all') or r['status'] == status)
            and (not needle or needle in (r['owner'] or '').casefold()) and (not meeting or r['session_id'] == meeting)]
    rows.sort(key=lambda r: (r['due_sort'] is None, r['due_sort'] or '', r['meeting_date'] or ''), reverse=False)
    undated = [r for r in rows if r['due_sort'] is None]
    undated.sort(key=lambda r: r['meeting_date'] or '', reverse=True)
    return {'actions': [r for r in rows if r['due_sort'] is not None] + undated, 'owners': owners,
            'meetings': sorted(meetings.values(), key=lambda m: m['date'] or '', reverse=True)}


def set_action_status(session_id, action_id, status):
    """Mark one action open or done. Keeps updated_at so the conversation list order is not disturbed."""
    if status not in ACTION_STATUSES:
        raise ValueError('Status must be open or done.')
    session = load_session(session_id)
    if action_id not in {a['action_id'] for a in _action_rows(session)}:
        raise ValueError('That action does not exist in this meeting.')
    session.setdefault('action_state', {})[action_id] = {'status': status, 'updated_at': now()}
    write_json(session_path(session_id), session)
    return session


def list_sessions(query=None):
    result = []
    needle = (query or '').strip().casefold()
    for path in (DATA / 'sessions').glob('*.json'):
        session = json.loads(path.read_text(encoding='utf-8'))
        if visible_session(session) and (not needle or session_matches(session, needle)):
            item = {k: session[k] for k in ('id', 'title', 'updated_at', 'channel_origin')}
            item.update(pinned=bool(session.get('pinned')), archived=bool(session.get('archived')))
            result.append(item)
    return sorted(result, key=lambda s: s['updated_at'], reverse=True)
