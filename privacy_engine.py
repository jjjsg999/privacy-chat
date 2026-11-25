"""Custom terms first, then local Presidio/spaCy detection.

Two detector modes (setting `privacy_detectors`):
  standard  today's behaviour: terms list, then Presidio (patterns + spaCy).
  layered   terms > Presidio patterns > OpenAI Privacy Filter > spaCy, merged by union.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import public_institutions
import public_topics
from public_places import PUBLIC_PLACES, is_public_place

DATE_MODES = ('pass', 'mask')  # date_names: dates pass through (default) or are masked as DATE_TIME (the old behaviour)
# spaCy also labels digit groups such as account numbers ("6602 1187 4093") as DATE_TIME. Only findings shaped like a
# date pass: anything with a letter ("this week", "3 March 2026", "Q3"), a bare year, a numeric date or a time.
_DATE_SHAPES = re.compile(r'(?:19|20)\d\d|\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}[-/.]\d{1,2}|\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?|'
                          r'\d{1,2}(?:st|nd|rd|th)', re.I)


def looks_like_date(text):
    text = text.strip()
    if re.search('[' + CJK + ']', text):  # spaCy can swallow a Chinese name into a date span: keep those masked
        return False
    return bool(re.search(r'[^\W\d_]', text)) or _DATE_SHAPES.fullmatch(text) is not None

# Bump when masking behaviour changes in a way that should invalidate every cached masked text.
PRIVACY_VERSION = '1'

# Word boundaries that treat CJK as separators: Chinese/Japanese/Korean text has no spaces, so a
# placeholder or term glued to it (PERSON__3跟, 跟Acme開會) must still match.
CJK = '\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef'
BOUND_L = r'(?<![^\W' + CJK + r'])'
BOUND_R = r'(?![^\W' + CJK + r'])'
TOKEN = re.compile(BOUND_L + r'[A-Z][A-Z0-9_]*__\d+' + BOUND_R)

MODES = ('standard', 'layered')
# place_names: mask every LOCATION, or let countries, regions and major cities through.
PLACE_MODES = ('common_public', 'mask_all')

# Layer priority when overlapping spans disagree about the kind (lower wins).
PRIORITY = {'terms': 0, 'pattern': 1, 'privacy_filter': 2, 'spacy': 3}

# Privacy Filter label -> existing token kind. New kinds only where none existed.
# private_date is deliberately absent: date handling stays as today (spaCy DATE_TIME).
PRIVACY_FILTER_LABELS = {
    'private_person': 'PERSON',
    'private_email': 'EMAIL_ADDRESS',
    'private_phone': 'PHONE_NUMBER',
    'private_url': 'URL',
    'account_number': 'ACCOUNT_NUMBER',  # new
    'private_address': 'ADDRESS',        # new
    'secret': 'SECRET',                  # new
}

DEFAULT_MODEL_DIR = Path(os.environ.get('PRIVACY_FILTER_DIR') or Path(__file__).resolve().parent / 'models' / 'privacy-filter')
MODEL_FILE = 'onnx/model_quantized.onnx'
CHUNK_TOKENS, CHUNK_OVERLAP = 1024, 128
_NEG_INF = -1e9


class PrivacyFilterUnavailable(RuntimeError):
    """The layered filter was requested but a detector cannot run. Never fall back."""


def merge_spans(spans):
    """Union overlapping spans into one; the highest-priority member decides the kind.

    spans: iterable of (start, end, kind, source). Touching spans stay separate.
    Returns sorted (start, end, kind, source) tuples with no overlaps.
    """
    merged = []
    for span in sorted((s for s in spans if s[1] > s[0]), key=lambda s: (s[0], -s[1])):
        if merged and span[0] < merged[-1]['end']:
            group = merged[-1]
            group['end'] = max(group['end'], span[1])
            group['members'].append(span)
        else:
            merged.append({'start': span[0], 'end': span[1], 'members': [span]})
    result = []
    for group in merged:
        # Best priority wins; ties go to the longer member, then the earlier one.
        _, _, kind, source = min(group['members'], key=lambda s: (PRIORITY[s[3]], -(s[1] - s[0]), s[0]))
        result.append((group['start'], group['end'], kind, source))
    return result


class PrivacyFilter:
    """OpenAI Privacy Filter on ONNX Runtime (no PyTorch) with constrained Viterbi decoding."""

    def __init__(self, model_dir=None):
        self.model_dir = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        try:
            import numpy
            import onnxruntime
            from tokenizers import Tokenizer
            self.np = numpy
            model_path = self.model_dir / MODEL_FILE
            if not model_path.exists():
                raise FileNotFoundError(f'{model_path} not found')
            config = json.loads((self.model_dir / 'config.json').read_text(encoding='utf-8'))
            self.labels = [config['id2label'][str(i)] for i in range(len(config['id2label']))]
            self.tokenizer = Tokenizer.from_file(str(self.model_dir / 'tokenizer.json'))
            self.session = onnxruntime.InferenceSession(str(model_path), providers=['CPUExecutionProvider'])
            self.input_names = {item.name for item in self.session.get_inputs()}
            self._build_decoder(json.loads((self.model_dir / 'viterbi_calibration.json').read_text(encoding='utf-8')))
        except Exception as exc:
            raise PrivacyFilterUnavailable(
                f'Layered privacy filter cannot load ({type(exc).__name__}: {exc}). Nothing was sent. '
                'Download the model with: python -m eval.download_privacy_filter, '
                'or set privacy_detectors back to "standard".') from exc

    def _build_decoder(self, calibration):
        np = self.np
        biases = calibration['operating_points']['default']['biases']
        count = len(self.labels)
        parts = [(None, None) if name == 'O' else tuple(name.split('-', 1)) for name in self.labels]
        self._start = np.full(count, _NEG_INF, dtype=np.float64)
        self._end = np.full(count, _NEG_INF, dtype=np.float64)
        self._trans = np.full((count, count), _NEG_INF, dtype=np.float64)
        for i, (tag, label) in enumerate(parts):
            if tag in (None, 'B', 'S'):
                self._start[i] = 0.0
            if tag in (None, 'E', 'S'):
                self._end[i] = 0.0
            for j, (next_tag, next_label) in enumerate(parts):
                if tag in (None, 'E', 'S'):
                    if next_tag is None:
                        self._trans[i, j] = biases['transition_bias_background_stay'] if tag is None else biases['transition_bias_end_to_background']
                    elif next_tag in ('B', 'S'):
                        self._trans[i, j] = biases['transition_bias_background_to_start'] if tag is None else biases['transition_bias_end_to_start']
                elif next_label == label and next_tag in ('I', 'E'):
                    self._trans[i, j] = biases['transition_bias_inside_to_continue'] if next_tag == 'I' else biases['transition_bias_inside_to_end']

    def _viterbi(self, logprobs):
        np = self.np
        steps = logprobs.shape[0]
        scores = logprobs[0] + self._start
        back = np.zeros((max(steps - 1, 0), logprobs.shape[1]), dtype=np.int64)
        for i in range(1, steps):
            candidates = scores[:, None] + self._trans
            back[i - 1] = candidates.argmax(axis=0)
            scores = candidates.max(axis=0) + logprobs[i]
        scores = scores + self._end
        path = [int(scores.argmax())]
        for i in range(steps - 2, -1, -1):
            path.append(int(back[i, path[-1]]))
        return path[::-1]

    def _logits(self, ids):
        np = self.np
        feeds = {'input_ids': np.array([ids], dtype=np.int64)}
        if 'attention_mask' in self.input_names:
            feeds['attention_mask'] = np.ones((1, len(ids)), dtype=np.int64)
        logits = self.session.run(None, feeds)[0][0].astype(np.float64)
        logits -= logits.max(axis=1, keepdims=True)
        return logits - np.log(np.exp(logits).sum(axis=1, keepdims=True))

    def detect(self, text):
        """Return [(start, end, label)] over `text` for the model's raw labels (e.g. private_person)."""
        if not text.strip():
            return []
        encoding = self.tokenizer.encode(text, add_special_tokens=False)
        ids, offsets = encoding.ids, encoding.offsets
        found, begin = [], 0
        while begin < len(ids):
            stop = min(begin + CHUNK_TOKENS, len(ids))
            path = self._viterbi(self._logits(ids[begin:stop]))
            first = None
            for i, label_id in enumerate(path):
                tag, _, label = (self.labels[label_id].partition('-'))
                if self.labels[label_id] == 'O':
                    first = None
                    continue
                if tag in ('B', 'S'):
                    first = begin + i
                if tag in ('E', 'S') and first is not None:
                    start, end = offsets[first][0], offsets[begin + i][1]
                    while start < end and text[start].isspace():
                        start += 1
                    while end > start and text[end - 1].isspace():
                        end -= 1
                    found.append((start, end, label))
                    first = None
            if stop == len(ids):
                break
            begin = stop - CHUNK_OVERLAP
        return found


_filters = {}


def load_privacy_filter(model_dir=None):
    key = str(model_dir or DEFAULT_MODEL_DIR)
    if key not in _filters:
        _filters[key] = PrivacyFilter(model_dir)
    return _filters[key]


def parse_terms(text):
    terms = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*):\s*(.+)', line)
        terms.append((match[1], match[2].strip()) if match else ('SENSITIVE', line))
    return sorted(terms, key=lambda pair: len(pair[1]), reverse=True)


def terms_pattern(terms):
    """(compiled pattern, {group name: kind}) for parsed terms: whole words (CJK-aware), ignoring case."""
    lookup = {f'T{i}': kind for i, (kind, _) in enumerate(terms)}
    pattern = re.compile(BOUND_L + '(?:' + '|'.join(f'(?P<T{i}>{re.escape(value)})' for i, (_, value) in enumerate(terms)) + ')' + BOUND_R, re.I)
    return pattern, lookup


def allocate_token(token_map, kind, value, text):
    """The placeholder for `value`: its existing token, or a new KIND__n that is free in the map and in `text`."""
    for token, original in token_map.items():
        if original == value:
            return token
    token = f'{kind}__{len(token_map) + 1}'
    while token in token_map or token in text:
        token = f'{kind}__{int(token.rsplit("__", 1)[1]) + 1}'
    token_map[token] = value
    return token


def term_matches(value, terms):
    """True if `value` is one of the terms, using the same matching as masking (case, boundaries, CJK)."""
    if not terms or not value:
        return False
    return terms_pattern(terms)[0].fullmatch(value) is not None


class PrivacyEngine:
    def __init__(self, analyzer=None, mode='standard', privacy_filter=None, place_names='common_public',
                 institution_names='common_public', institutions=None, date_names='pass', topic_names='common_public',
                 topics=None, public_names=None):
        self.mode = mode
        self.place_names = place_names
        self.institution_names = institution_names
        self.institutions = public_institutions.effective_list() if institutions is None else institutions
        self.date_names = date_names
        self.topic_names = topic_names
        self.topics = public_topics.effective_list() if topics is None else topics
        self.public_names = public_names or []
        self._privacy_filter = privacy_filter
        if analyzer is None:
            import spacy.util
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            from presidio_analyzer.predefined_recognizers import EmailRecognizer

            class OfflineEmailRecognizer(EmailRecognizer):
                def validate_result(self, pattern_text):
                    # Keep regex confidence without a public-suffix network lookup.
                    return None
            # Check locally first: never silently download models or bypass privacy.
            if not spacy.util.is_package('en_core_web_sm'):
                raise RuntimeError('Install the English model: python -m spacy download en_core_web_sm')
            nlp = NlpEngineProvider(nlp_configuration={
                'nlp_engine_name': 'spacy',
                'models': [{'lang_code': 'en', 'model_name': 'en_core_web_sm'}]
            }).create_engine()
            analyzer = AnalyzerEngine(nlp_engine=nlp, supported_languages=['en'])
            analyzer.registry.remove_recognizer('EmailRecognizer')
            analyzer.registry.add_recognizer(OfflineEmailRecognizer())
        self.analyzer = analyzer

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        value = 'standard' if value is None else value
        if value not in MODES:
            # A typo must never silently weaken or change the filter.
            raise ValueError(f'privacy_detectors must be one of {MODES}, got {value!r}')
        self._mode = value

    @property
    def place_names(self):
        return self._place_names

    @place_names.setter
    def place_names(self, value):
        value = 'common_public' if value is None else value
        if value not in PLACE_MODES:
            raise ValueError(f'place_names must be one of {PLACE_MODES}, got {value!r}')
        self._place_names = value

    @property
    def institution_names(self):
        return self._institution_names

    @institution_names.setter
    def institution_names(self, value):
        value = 'common_public' if value is None else value
        if value not in public_institutions.MODES:
            raise ValueError(f'institution_names must be one of {public_institutions.MODES}, got {value!r}')
        self._institution_names = value

    @property
    def institutions(self):
        return self._institutions

    @institutions.setter
    def institutions(self, names):
        self._institutions = list(names or [])
        self._institution_re = public_institutions.pattern(self._institutions, BOUND_L, BOUND_R)

    @property
    def date_names(self):
        return self._date_names

    @date_names.setter
    def date_names(self, value):
        value = 'pass' if value is None else value
        if value not in DATE_MODES:
            raise ValueError(f'date_names must be one of {DATE_MODES}, got {value!r}')
        self._date_names = value

    @property
    def topic_names(self):
        return self._topic_names

    @topic_names.setter
    def topic_names(self, value):
        value = 'common_public' if value is None else value
        if value not in public_topics.MODES:
            raise ValueError(f'topic_names must be one of {public_topics.MODES}, got {value!r}')
        self._topic_names = value

    @property
    def topics(self):
        return self._topics

    @topics.setter
    def topics(self, names):
        self._topics = list(names or [])
        self._topic_re = public_institutions.pattern(self._topics, BOUND_L, BOUND_R)

    @property
    def public_names(self):
        return self._public_names

    @public_names.setter
    def public_names(self, names):
        self._public_names = list(names or [])
        self._public_name_re = public_institutions.pattern(self._public_names, BOUND_L, BOUND_R, ignore_case=True)

    def _public_patterns(self):
        """The public vocabulary in force: institutions and topics (unless mask_all) and the user's public names."""
        found = []
        if self.institution_names != 'mask_all' and self._institution_re is not None:
            found.append(self._institution_re)
        if self.topic_names != 'mask_all' and self._topic_re is not None:
            found.append(self._topic_re)
        if self._public_name_re is not None:
            found.append(self._public_name_re)
        return found

    def _public_institution_spans(self, text):
        """[(start, end)] of public vocabulary in `text`: institutions, glossary topics and the user's public names."""
        return [(m.start(), m.end()) for pattern in self._public_patterns() for m in pattern.finditer(text)]

    def is_public(self, value):
        """True if `value` is exactly an entry of the public vocabulary in force (for web text and the query check)."""
        return any(pattern.fullmatch(value.strip()) for pattern in self._public_patterns())

    @staticmethod
    def _inside(start, end, spans):
        return any(s <= start and end <= e for s, e in spans)

    def _detect(self, text):
        findings = self.analyzer.analyze(text=text, language='en')
        if self.date_names == 'pass':  # dates are not private on their own; the terms list can still mask one
            findings = [f for f in findings if not (f.entity_type == 'DATE_TIME' and looks_like_date(text[f.start:f.end]))]
        if self.place_names != 'mask_all':
            findings = [f for f in findings if not (f.entity_type == 'LOCATION' and is_public_place(text[f.start:f.end]))]
        public = self._public_institution_spans(text)
        if public:  # a finding inside public vocabulary (MAS, Digital Asset Custody, a public name) stays readable
            findings = [f for f in findings if not self._inside(f.start, f.end, public)]
        return findings

    def fingerprint(self, terms_text=''):
        """Short hash of everything that decides what masking does: the terms list, the detector setup and versions.

        Masked text cached under one fingerprint is only reused under the same one.
        """
        from importlib import metadata
        parts = [PRIVACY_VERSION, self.mode, self.place_names, terms_text or '']
        if self.place_names == 'common_public':
            parts.append(hashlib.sha256('\n'.join(sorted(PUBLIC_PLACES)).encode('utf-8')).hexdigest())
        parts.append('institutions:' + self.institution_names)
        if self.institution_names == 'common_public':
            parts.append(hashlib.sha256('\n'.join(sorted(self.institutions)).encode('utf-8')).hexdigest())
        parts.append('dates:' + self.date_names)
        parts.append('topics:' + self.topic_names)
        if self.topic_names == 'common_public':
            parts.append(hashlib.sha256('\n'.join(sorted(self.topics)).encode('utf-8')).hexdigest())
        if self.public_names:
            parts.append('public:' + hashlib.sha256('\n'.join(sorted(self.public_names)).encode('utf-8')).hexdigest())
        for package in ('presidio-analyzer', 'spacy', 'en-core-web-sm'):
            try:
                parts.append(package + metadata.version(package))
            except metadata.PackageNotFoundError:
                parts.append(package + '?')
        if self.mode == 'layered':
            model = DEFAULT_MODEL_DIR / MODEL_FILE
            parts.append(f'privacy-filter:{model.stat().st_size if model.exists() else "missing"}')
        return hashlib.sha256('\x1f'.join(parts).encode('utf-8')).hexdigest()[:16]

    def final_spans(self, text, terms):
        """Layered detection. Returns merged [(start, end, kind, source)] over `text`.

        This is the function the masking itself uses. Raises PrivacyFilterUnavailable
        (never falls back) if a detector cannot run. Placeholder-shaped text is protected.
        """
        protected = [(m.start(), m.end()) for m in TOKEN.finditer(text)]
        blank = list(text)
        for start, end in protected:
            blank[start:end] = ' ' * (end - start)
        blank = ''.join(blank)
        spans = []
        if terms:
            pattern, lookup = terms_pattern(terms)
            spans += [(m.start(), m.end(), lookup[m.lastgroup], 'terms') for m in pattern.finditer(blank)]
        for item in self._detect(blank):
            metadata = getattr(item, 'recognition_metadata', None) or {}
            source = 'spacy' if metadata.get('recognizer_name') == 'SpacyRecognizer' else 'pattern'
            spans.append((item.start, item.end, item.entity_type, source))
        if self._privacy_filter is None:
            self._privacy_filter = load_privacy_filter()
        institutions = self._public_institution_spans(blank)  # the terms list still wins: its spans are never dropped
        for start, end, label in self._privacy_filter.detect(blank):
            if label in PRIVACY_FILTER_LABELS and not self._inside(start, end, institutions):  # private_date is ignored this round
                spans.append((start, end, PRIVACY_FILTER_LABELS[label], 'privacy_filter'))
        spans = [s for s in spans if not any(s[0] < end and s[1] > start for start, end in protected)]
        return merge_spans(spans)

    def tokenize(self, text, token_map, terms):
        def allocate(kind, value):
            return allocate_token(token_map, kind, value, text)  # `text` is read at call time, as before

        # Reserve literal placeholder-shaped user text to preserve round trips.
        text = TOKEN.sub(lambda m: allocate('LITERAL', m[0]), text)
        if self.mode == 'layered':
            for start, end, kind, _ in reversed(self.final_spans(text, terms)):
                text = text[:start] + allocate(kind, text[start:end]) + text[end:]
            return text
        if terms:
            pattern, lookup = terms_pattern(terms)
            parts, cursor = [], 0
            for token_match in TOKEN.finditer(text):
                parts.append(pattern.sub(lambda m: allocate(lookup[m.lastgroup], m[0]), text[cursor:token_match.start()]))
                parts.append(token_match[0])
                cursor = token_match.end()
            parts.append(pattern.sub(lambda m: allocate(lookup[m.lastgroup], m[0]), text[cursor:]))
            text = ''.join(parts)
        protected = [(m.start(), m.end()) for m in TOKEN.finditer(text)]
        # Mask tokens before detection so their numbers cannot be mistaken for PII.
        masked = list(text)
        for start, end in protected:
            masked[start:end] = ' ' * (end - start)
        findings = self._detect(''.join(masked))
        selected = []
        for item in sorted(findings, key=lambda x: (x.start, -x.end, -x.score)):
            if any(item.start < end and item.end > start for start, end in protected):
                continue
            if selected and item.start < selected[-1][1]:
                selected[-1][1] = max(selected[-1][1], item.end)
            else:
                selected.append([item.start, item.end, item.entity_type])
        for start, end, kind in reversed(selected):
            text = text[:start] + allocate(kind, text[start:end]) + text[end:]
        return text

    def mask_web_text(self, text, token_map, terms):
        """Mask public web text against what this chat already protects, and nothing more.

        1. placeholder-shaped text is reserved as LITERAL (as in tokenize), 2. every real value already in the chat's
        token map becomes its placeholder, 3. the terms list is applied (new matches get tokens). The detectors are
        never run: web text is public, and detecting names in it would mask public names and cost seconds per page.
        """
        def allocate(kind, value):
            return allocate_token(token_map, kind, value, text)

        def outside_tokens(pattern, replace):
            parts, cursor = [], 0
            for token_match in TOKEN.finditer(text):
                parts.append(pattern.sub(replace, text[cursor:token_match.start()]))
                parts.append(token_match[0])
                cursor = token_match.end()
            parts.append(pattern.sub(replace, text[cursor:]))
            return ''.join(parts)

        text = TOKEN.sub(lambda m: allocate('LITERAL', m[0]), text or '')
        # Known values go first so a differently-cased mention ("ACME LTD") reuses the chat's token instead of a new one.
        # Values that are public now (a date while dates pass, a name since treated as public) stay readable.
        known = sorted(((token, value) for token, value in token_map.items()
                        if not token.startswith('LITERAL__') and isinstance(value, str) and value.strip()
                        and not (self.date_names == 'pass' and token.startswith('DATE_TIME__'))
                        and not self.is_public(value)),
                       key=lambda pair: len(pair[1]), reverse=True)
        if known:
            by_value = {}
            for token, value in known:
                by_value.setdefault(value.casefold(), token)
            pattern = re.compile(BOUND_L + '(?:' + '|'.join(re.escape(value) for _, value in known) + ')' + BOUND_R, re.I)
            text = outside_tokens(pattern, lambda m: by_value.get(m[0].casefold()) or allocate('SENSITIVE', m[0]))
        if terms:
            pattern, lookup = terms_pattern(terms)
            text = outside_tokens(pattern, lambda m: allocate(lookup[m.lastgroup], m[0]))
        return text

    @staticmethod
    def rehydrate(text, token_map):
        return TOKEN.sub(lambda m: token_map.get(m[0], m[0]), text)


class StreamRehydrator:
    """Incremental rehydrate(): holds back a trailing word so split tokens restore correctly.

    Concatenated feed()+flush() output equals PrivacyEngine.rehydrate(full_text, token_map).
    """
    MAX_HELD = 200  # Placeholders are short; a longer unbroken run cannot be one.

    def __init__(self, token_map):
        self.token_map = token_map
        self.buffer = ''

    def feed(self, chunk):
        self.buffer += chunk
        tail = re.search(r'[^\W' + CJK + r']+$', self.buffer)
        cut = tail.start() if tail and len(tail[0]) <= self.MAX_HELD else len(self.buffer)
        ready, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return PrivacyEngine.rehydrate(ready, self.token_map)

    def flush(self):
        ready, self.buffer = self.buffer, ''
        return PrivacyEngine.rehydrate(ready, self.token_map)
