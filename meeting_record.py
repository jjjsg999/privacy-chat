"""Structured meeting record: numbered lines, the schema, local verification, Telegram text, Q&A line references.

Pure functions with no model calls. The meeting model returns JSON; everything here is checked on this machine:
line numbers must exist, owners and figures must appear in the lines they cite, and ids are assigned locally.
Failures are flagged on the item (never dropped), so nothing the model said disappears silently.
"""
import json
import re
from datetime import date, datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

SCHEMA_VERSION = 1
SECTIONS = (('decisions', 'D'), ('actions', 'A'), ('open_questions', 'Q'), ('risks', 'R'), ('key_figures', 'F'))
SECTION_TITLES = {'decisions': 'Decisions', 'actions': 'Actions', 'open_questions': 'Open questions',
                  'risks': 'Risks', 'key_figures': 'Key figures'}
CJK = re.compile('[⺀-鿿가-힯豈-﫿＀-￯]')


class RecordError(ValueError):
    """The model's output is not a valid record. The message is safe to show and to send back to the model."""


# ---------------------------------------------------------------- numbered transcript

def clean_lines(text):
    """Light clean-up only: drop empty lines and exact consecutive duplicates (from batching). Words never change."""
    lines = []
    for raw in re.split(r'\r\n|\r|\n', text or ''):
        line = raw.strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return lines


def numbered(lines):
    """The text sent to the model: line N of the stored transcript is line N here, whatever masking did inside a line."""
    return '\n'.join(f'L{n}: {line}' for n, line in enumerate(lines, 1))


# ---------------------------------------------------------------- schema

def _line_numbers(value):
    if not isinstance(value, list):
        raise ValueError('lines must be a list of line numbers')
    out = []
    for entry in value:
        if isinstance(entry, bool):
            raise ValueError('lines must be numbers')
        if isinstance(entry, int):
            out.append(entry)
        elif isinstance(entry, str) and re.fullmatch(r'\s*L?(\d+)\s*', entry, re.I):
            out.append(int(re.sub(r'\D', '', entry)))
        else:
            raise ValueError('lines must be numbers such as 12')
    return out


def _blank_to_none(value):
    return None if isinstance(value, str) and not value.strip() else value


Lines = Annotated[list[int], BeforeValidator(_line_numbers), Field(max_length=50)]
Text = Annotated[str, Field(min_length=1, max_length=4000)]
def _short(value):
    if value is not None and len(value) > 500:
        raise ValueError('must be at most 500 characters')
    return value


Optional = Annotated[str | None, BeforeValidator(_blank_to_none), AfterValidator(_short)]


class _Item(BaseModel):
    model_config = ConfigDict(extra='ignore')


class Decision(_Item):
    text: Text
    lines: Lines = []


class Action(_Item):
    task: Text
    owner: Optional = None
    due: Optional = None
    lines: Lines = []


class Figure(_Item):
    figure: Annotated[str, Field(min_length=1, max_length=200)]
    context: Annotated[str, Field(max_length=2000)] = ''
    lines: Lines = []


class Record(_Item):
    brief: Annotated[str, Field(min_length=1, max_length=40000)]
    decisions: Annotated[list[Decision], Field(max_length=100)] = []
    actions: Annotated[list[Action], Field(max_length=100)] = []
    open_questions: Annotated[list[Decision], Field(max_length=100)] = []
    risks: Annotated[list[Decision], Field(max_length=100)] = []
    key_figures: Annotated[list[Figure], Field(max_length=100)] = []


def _json_object(raw):
    text = (raw or '').strip()
    fenced = re.match(r'^```(?:json)?\s*\n(.*?)\n```\s*$', text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find('{'), text.rfind('}')
        if start >= 0 < end:
            try:
                return json.loads(text[start:end + 1])
            except ValueError as exc:
                raise RecordError(f'The reply is not valid JSON ({exc.msg} at character {exc.pos}).') from None
        raise RecordError('The reply is not valid JSON: no JSON object was found.') from None


def parse_record(raw):
    """Validate the model's reply. Raises RecordError with a short reason (no model text is echoed back)."""
    data = _json_object(raw)
    if not isinstance(data, dict):
        raise RecordError('The reply must be one JSON object.')
    try:
        return Record.model_validate(data)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}" for e in exc.errors()[:8]]
        raise RecordError('The JSON does not match the schema: ' + '; '.join(problems)) from None


def fallback_brief(raw):
    """Prose for a reply that never validated: its brief if it has one, otherwise the reply as written."""
    try:
        data = _json_object(raw)
        if isinstance(data, dict) and isinstance(data.get('brief'), str) and data['brief'].strip():
            return data['brief']
    except RecordError:
        pass
    return (raw or '').strip()


# ---------------------------------------------------------------- local verification

def owner_found(owner, texts):
    """Some part of the owner's name (2+ characters; the full name for CJK names) appears in a cited line."""
    haystack = '\n'.join(texts).casefold()
    name = owner.strip().casefold()
    if CJK.search(name):
        return name in haystack
    return any(len(part) >= 2 and part in haystack for part in re.split(r'[\s,.;:()\-_/]+', name))


def _compact(text):
    return re.sub(r'[,\s]', '', text)


def figure_problem(figure, texts):
    numbers = re.findall(r'\d+(?:\.\d+)?', _compact(figure))
    if not numbers:
        return 'figure has no digits to check'
    haystack = _compact('\n'.join(texts))
    missing = [n for n in numbers if n not in haystack]
    return f"figure {', '.join(missing)} not found in cited lines" if missing else None


def verify(section, item, lines):
    """(verified, reason or None) for one item against the stored transcript lines."""
    reasons, cited = [], item.get('lines') or []
    if not cited:
        reasons.append('no line cited')
    bad = [n for n in cited if not 1 <= n <= len(lines)]
    if bad:
        reasons.append('cites line ' + ', '.join(f'L{n}' for n in bad) + ' which does not exist')
    texts = [lines[n - 1] for n in cited if 1 <= n <= len(lines)]
    if section == 'actions' and item.get('owner') and not owner_found(item['owner'], texts):
        reasons.append('owner not found in cited lines')
    if section == 'key_figures':
        problem = figure_problem(item['figure'], texts)
        if problem:
            reasons.append(problem)
    return (not reasons, '; '.join(reasons) or None)


def finalize(record, lines, restore, *, model, at):
    """The stored record: ids assigned here, placeholders restored in every string, every item verified."""
    out = {'schema': SCHEMA_VERSION, 'model': model, 'at': at, 'structured': True, 'failure': None,
           'brief': restore(record.brief)}
    for section, prefix in SECTIONS:
        items = []
        for number, entry in enumerate(getattr(record, section), 1):
            item = {key: (restore(value) if isinstance(value, str) else value) for key, value in entry.model_dump().items()}
            item['lines'] = sorted(set(item['lines']))
            item['id'] = f'{prefix}{number}'
            item['verified'], item['reason'] = verify(section, item, lines)
            items.append(item)
        out[section] = items
    return out


def prose_record(brief, *, model, at, failure):
    """A record whose structured part failed: the brief is kept as prose, with the reason."""
    out = {'schema': SCHEMA_VERSION, 'model': model, 'at': at, 'structured': False, 'failure': failure, 'brief': brief}
    out.update({section: [] for section, _ in SECTIONS})
    return out


def brief_of(intelligence):
    """The brief from a stored record, or the old prose analysis itself."""
    return intelligence.get('brief', '') if isinstance(intelligence, dict) else str(intelligence or '')


def unverified_count(record):
    return sum(1 for section, _ in SECTIONS for item in record.get(section, []) if not item.get('verified', True))


# ---------------------------------------------------------------- Q&A references

REF = re.compile(r'\[L(\d+)\]')


def find_refs(text, line_count):
    """[(start, end, number, valid)] for each [Lnn] in an answer. Invalid ones are shown as plain text with a marker."""
    return [(m.start(), m.end(), int(m.group(1)), 1 <= int(m.group(1)) <= line_count) for m in REF.finditer(text or '')]


# ---------------------------------------------------------------- due dates (common formats only)

_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %B %Y', '%d %b %Y', '%B %d %Y', '%b %d %Y')
_NO_YEAR = ('%d %B', '%d %b', '%B %d', '%b %d')


def parse_due(text, reference):
    """A date for sorting, or None. Slash dates are day-first. A date with no year takes the meeting's year,
    or the next one when that would put it well before the meeting."""
    if not text:
        return None
    cleaned = re.sub(r'(?<=\d)(st|nd|rd|th)\b', '', text.strip(), flags=re.I)
    cleaned = re.sub(r'^(mon|tues?|wed(nes)?|thu(rs)?|fri|sat(ur)?|sun)[a-z]*,?\s+', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\bsept\b', 'Sep', cleaned.replace(',', ' '), flags=re.I)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .')
    for pattern in _FORMATS:
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            pass
    for pattern in _NO_YEAR:
        try:
            parsed = datetime.strptime(f'{cleaned} 2000' if '%d' in pattern else cleaned + ' 2000', pattern + ' %Y').date()
        except ValueError:
            continue
        try:
            guess = parsed.replace(year=reference.year)
            return guess.replace(year=guess.year + 1) if guess < reference - timedelta(days=180) else guess
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- Telegram text

LIMIT = 4000  # Telegram allows 4,096 UTF-16 units; leave room


def _units(text):
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)


def split_text(text, limit=LIMIT):
    """Split into messages of at most `limit` UTF-16 units, preferring line breaks."""
    messages, current, size = [], [], 0
    for line in text.split('\n'):
        pieces = [line]
        if _units(line) > limit:  # one very long line: cut it hard
            pieces, buffer, used = [], [], 0
            for char in line:
                step = 2 if ord(char) > 0xFFFF else 1
                if used + step > limit:
                    pieces.append(''.join(buffer))
                    buffer, used = [], 0
                buffer.append(char)
                used += step
            pieces.append(''.join(buffer))
        for piece in pieces:
            cost = _units(piece) + 1
            if current and size + cost > limit:
                messages.append('\n'.join(current))
                current, size = [], 0
            current.append(piece)
            size += cost
    if current:
        messages.append('\n'.join(current))
    return [m for m in messages if m.strip()]


def _plain(markdown):
    return re.sub(r'^\s{0,3}#{1,6}\s*', '', re.sub(r'[*_`]+', '', markdown), flags=re.M)


def _refs(item):
    return ' [' + ', '.join(f'L{n}' for n in item['lines']) + ']' if item.get('lines') else ''


def telegram_messages(record, brief_lines=8):
    """Compact plain-text summary, split to fit Telegram. A legacy prose analysis is returned as it was."""
    if not isinstance(record, dict):
        return split_text(str(record or ''))
    brief = [line for line in _plain(record.get('brief', '')).splitlines() if line.strip()]
    parts = ['\n'.join(brief[:brief_lines]) + ('\n…' if len(brief) > brief_lines else '')]
    flag = lambda item: '' if item.get('verified', True) else ' (unverified)'
    if record.get('decisions'):
        parts.append('Decisions\n' + '\n'.join(f"• {d['text']}{_refs(d)}{flag(d)}" for d in record['decisions']))
    if record.get('actions'):
        parts.append('Actions\n' + '\n'.join(
            f"• {a['owner'] or 'No owner'}: {a['task']} (due {a['due'] or 'not stated'}){_refs(a)}{flag(a)}" for a in record['actions']))
    extras = [f"{len(record[s])} {SECTION_TITLES[s].lower()}" for s in ('open_questions', 'risks', 'key_figures') if record.get(s)]
    if extras:
        parts.append('Also recorded: ' + ', '.join(extras) + '. Open the meeting in Web to see them.')
    if not record.get('structured', True):
        parts.append(f"Structured part failed: {record.get('failure') or 'unknown reason'}. The brief above is prose.")
    else:
        flagged = unverified_count(record)
        parts.append(f'{flagged} item{"s" if flagged != 1 else ""} unverified against the transcript.' if flagged
                     else 'All items verified against the transcript.')
    return split_text('\n\n'.join(p for p in parts if p))


def telegram_actions(actions):
    """Open actions as plain text, split to fit Telegram."""
    if not actions:
        return ['No open actions.']
    lines = [f"Open actions ({len(actions)})"]
    for a in actions:
        lines.append(f"• {a['owner'] or 'No owner'}: {a['task']} (due {a['due'] or 'not stated'}) — {a['meeting_title']}")
    return split_text('\n'.join(lines))
