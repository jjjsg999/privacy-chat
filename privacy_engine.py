"""Custom terms first, then local Presidio/spaCy detection."""
import re

TOKEN = re.compile(r'(?<!\w)[A-Z][A-Z0-9_]*__\d+(?!\w)')


def parse_terms(text):
    terms = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*):\s*(.+)', line)
        terms.append((match[1], match[2].strip()) if match else ('SENSITIVE', line))
    return sorted(terms, key=lambda pair: len(pair[1]), reverse=True)


class PrivacyEngine:
    def __init__(self, analyzer=None):
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

    def tokenize(self, text, token_map, terms):
        def allocate(kind, value):
            for token, original in token_map.items():
                if original == value:
                    return token
            token = f'{kind}__{len(token_map) + 1}'
            while token in token_map or token in text:
                token = f'{kind}__{int(token.rsplit("__", 1)[1]) + 1}'
            token_map[token] = value
            return token

        # Reserve literal placeholder-shaped user text to preserve round trips.
        text = TOKEN.sub(lambda m: allocate('LITERAL', m[0]), text)
        if terms:
            lookup = {f'T{i}': kind for i, (kind, _) in enumerate(terms)}
            pattern = re.compile(r'(?<!\w)(?:' + '|'.join(f'(?P<T{i}>{re.escape(value)})' for i, (_, value) in enumerate(terms)) + r')(?!\w)', re.I)
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
        findings = self.analyzer.analyze(text=''.join(masked), language='en')
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

    @staticmethod
    def rehydrate(text, token_map):
        return TOKEN.sub(lambda m: token_map.get(m[0], m[0]), text)
