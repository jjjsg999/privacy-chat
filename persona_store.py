"""Small inert text persona library; one atomic JSON file, never executable files."""
import copy
import json
import re
import threading
from uuid import uuid4

import storage

MAX_BYTES = 20_000
_lock = threading.RLock()


class PersonaError(ValueError):
    pass


def validate(name, description, filename, text):
    if (not isinstance(name, str) or not name.strip() or len(name) > 100
            or not isinstance(description, str) or len(description) > 500):
        raise PersonaError('Use a name up to 100 characters and description up to 500.')
    if (not isinstance(filename, str) or len(filename) > 100
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]*\.(?:md|txt)', filename, re.I)
            or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', filename.rsplit('.', 1)[0].strip(), re.I)):
        raise PersonaError('Use a plain .md/.txt filename without paths or executable extensions.')
    try:
        size = len(text.encode('utf-8')) if isinstance(text, str) else MAX_BYTES + 1
    except UnicodeError:
        raise PersonaError('Persona must be valid UTF-8 text.') from None
    if (not isinstance(text, str) or not text.strip() or size > MAX_BYTES
            or any(ord(char) < 32 and char not in '\n\r\t' for char in text) or '\x7f' in text):
        raise PersonaError('Persona must be nonempty UTF-8 text of at most 20,000 bytes, without binary controls.')


def defaults():
    specs = [
        ('persona_chief_of_staff', 'Chief of Staff', 'Advice, priorities and action coaching on request.',
         'chief_of_staff.md', 'Act as a Chief of Staff adviser on request. Help clarify priorities, decisions, dependencies and next actions using available evidence. Distinguish reported facts, uncertain interpretations and recommendations. Ask when material context is missing. Do not run reminders, score the user, send proactive notifications or claim to have performed actions. Advice does not change factual memory.'),
        ('persona_pfeffer_inspired', 'Jeffrey Pfeffer-inspired career adviser (AI simulation)',
         'An AI simulation of ideas/frameworks, not Jeffrey Pfeffer or an authentic representation.',
         'pfeffer_inspired.md', 'You are an AI simulation inspired by ideas and frameworks associated with Jeffrey Pfeffer, not the real person, not an authentic representation, and not speaking on his behalf. On request, examine organizational power, stakeholder incentives, visibility and career choices. Be clear about uncertainty and tradeoffs; never fabricate quotes or personal endorsements. Separate evidence from your recommendations. Do not manipulate factual memory or claim to contact anyone.')]
    return {'schema': 1, 'revision': 0, 'personas': {sid: {
        'id': sid, 'name': name, 'description': description, 'filename': filename, 'text': text,
        'revision': 1, 'archived': False, 'updated_at': None} for sid, name, description, filename, text in specs}}


def load():
    path = storage.DATA / 'personas.json'
    if not path.exists():
        return defaults()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['schema'] != 1 or type(data['revision']) is not int or not isinstance(data['personas'], dict):
            raise ValueError
        for sid, item in data['personas'].items():
            if (not re.fullmatch(r'persona_[a-z0-9_]+', sid) or item['id'] != sid
                    or type(item['archived']) is not bool or type(item['revision']) is not int):
                raise ValueError
            validate(item['name'], item['description'], item['filename'], item['text'])
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise PersonaError('Persona library is unreadable; existing file retained.') from None


def get(identifier):
    item = load()['personas'].get(identifier)
    if item is None or item['archived']:
        raise PersonaError('Persona not available.')
    return copy.deepcopy(item)


def save(*, expected_revision, name, description, filename, text, identifier=None):
    validate(name, description, filename, text)
    with _lock:
        data = load()
        if data['revision'] != expected_revision:
            raise PersonaError('Persona library changed; reload before saving.')
        if identifier is not None and (identifier not in data['personas'] or data['personas'][identifier]['archived']):
            raise PersonaError('Persona not available.')
        if identifier is None and len(data['personas']) >= 50:
            raise PersonaError('Persona library limit is 50 definitions.')
        identifier = identifier or 'persona_' + uuid4().hex
        old = data['personas'].get(identifier, {})
        item = {'id': identifier, 'name': name.strip(), 'description': description.strip(),
                'filename': filename, 'text': text, 'archived': False,
                'revision': old.get('revision', 0) + 1, 'updated_at': storage.now()}
        data['personas'][identifier] = item
        data['revision'] += 1
        storage.write_json(storage.DATA / 'personas.json', data)
        return {'revision': data['revision'], 'persona': copy.deepcopy(item)}


def archive(identifier, expected_revision):
    with _lock:
        data = load()
        if data['revision'] != expected_revision or identifier not in data['personas']:
            raise PersonaError('Persona missing or library changed; reload before archiving.')
        data['personas'][identifier].update(archived=True, updated_at=storage.now())
        data['personas'][identifier]['revision'] += 1
        data['revision'] += 1
        storage.write_json(storage.DATA / 'personas.json', data)
        return {'revision': data['revision'], 'archived': identifier}
