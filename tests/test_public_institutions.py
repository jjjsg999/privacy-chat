"""Public institutions list: offline, with fake detectors. Every name and value is invented or public."""
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import engine
import public_institutions
import storage
from privacy_engine import PrivacyEngine, parse_terms


class WordAnalyzer:
    """Reports every occurrence of the given words (case-sensitive) as an ORGANIZATION finding."""

    def __init__(self, *words, kind='ORGANIZATION'):
        self.words, self.kind = words, kind

    def analyze(self, text, language):
        found = []
        for word in self.words:
            found += [SimpleNamespace(start=m.start(), end=m.end(), score=.9, entity_type=self.kind,
                                      recognition_metadata={'recognizer_name': 'SpacyRecognizer'})
                      for m in re.finditer(re.escape(word), text)]
        return found


class FakeFilter:
    def __init__(self, *spans):
        self.spans = list(spans)

    def detect(self, text):
        return [(text.index(word), text.index(word) + len(word), 'private_person') for word in self.spans if word in text]


class InstitutionMaskingTests(unittest.TestCase):
    def mask(self, text, analyzer, terms='', **kw):
        token_map = {}
        return PrivacyEngine(analyzer, **kw).tokenize(text, token_map, parse_terms(terms)), token_map

    def test_public_institution_stays_readable(self):
        text, _ = self.mask('Ask MAS and the Bank of England about Zorvane Ltd.', WordAnalyzer('MAS', 'Bank of England', 'Zorvane Ltd'))
        self.assertIn('MAS', text)
        self.assertIn('Bank of England', text)
        self.assertNotIn('Zorvane', text)

    def test_acronyms_are_case_sensitive(self):
        text, token_map = self.mask('The sec desk spoke to the SEC.', WordAnalyzer('sec', 'SEC'))
        self.assertIn('the SEC.', text)               # the regulator stays
        self.assertIn('sec', token_map.values())      # ordinary "sec" is not treated as the regulator

    def test_full_names_ignore_case(self):
        text, _ = self.mask('a note from the federal reserve', WordAnalyzer('federal reserve'))
        self.assertIn('federal reserve', text)

    def test_only_findings_inside_the_name_are_released(self):
        # A finding that runs past the institution's name is still masked as a whole.
        text, token_map = self.mask('MAS Holdings Pte is a client', WordAnalyzer('MAS Holdings Pte'))
        self.assertNotIn('MAS Holdings', text)
        self.assertIn('MAS Holdings Pte', token_map.values())

    def test_terms_list_wins(self):
        text, token_map = self.mask('Call MAS today', WordAnalyzer('MAS'), terms='CLIENT: MAS')
        self.assertNotIn('MAS', text)
        self.assertEqual(token_map, {'CLIENT__1': 'MAS'})

    def test_mask_all_restores_old_behaviour(self):
        text, _ = self.mask('Ask MAS and the Bank of England', WordAnalyzer('MAS', 'Bank of England'), institution_names='mask_all')
        self.assertNotIn('MAS', text)
        self.assertNotIn('Bank of England', text)

    def test_layered_mode_releases_privacy_filter_spans_but_keeps_terms(self):
        privacy = PrivacyEngine(WordAnalyzer(), mode='layered', privacy_filter=FakeFilter('Fitch Ratings', 'Moody\'s'))
        token_map = {}
        text = privacy.tokenize('Fitch Ratings and Moody\'s disagree', token_map, parse_terms("RATER: Moody's"))
        self.assertIn('Fitch Ratings', text)
        self.assertNotIn("Moody's", text)

    def test_removed_and_added_names(self):
        names = public_institutions.effective_list(added=['Zentral Regulator Board'], removed=['mas'])
        self.assertNotIn('MAS', names)
        self.assertIn('Zentral Regulator Board', names)
        privacy = PrivacyEngine(WordAnalyzer('MAS', 'Zentral Regulator Board'), institutions=names)
        text = privacy.tokenize('MAS and the Zentral Regulator Board', {}, [])
        self.assertNotIn('MAS', text)
        self.assertIn('Zentral Regulator Board', text)

    def test_fingerprint_changes_with_the_setting_and_the_list(self):
        privacy = PrivacyEngine(WordAnalyzer())
        base = privacy.fingerprint('')
        privacy.institutions = public_institutions.effective_list(added=['Another Public Body'])
        changed_list = privacy.fingerprint('')
        self.assertNotEqual(base, changed_list)
        privacy.institution_names = 'mask_all'
        self.assertNotEqual(privacy.fingerprint(''), changed_list)

    def test_invalid_setting_is_refused(self):
        with self.assertRaises(ValueError):
            PrivacyEngine(WordAnalyzer(), institution_names='some')


class InstitutionSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'sensitive_terms.txt'), ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)

    def test_defaults(self):
        config = storage.settings()
        self.assertEqual(config['features'], {'web_search': False})
        self.assertEqual(config['institution_names'], 'common_public')
        self.assertEqual((config['public_institutions_added'], config['public_institutions_removed']), ([], []))
        self.assertEqual(config['web_search']['max_searches_per_message'], 3)
        self.assertEqual(config['web_search']['engine'], 'brave')

    def test_invalid_values_fall_back(self):
        storage.write_json(storage.DATA / 'settings.json', {
            'model': 'a/b', 'features': {'web_search': 'yes'}, 'institution_names': 'some',
            'public_institutions_added': ['  Body  One ', 3, 'body one'],
            'web_search': {'max_searches_per_message': 9, 'pages_per_search': 1, 'engine': 'google', 'cost_per_search_usd': True,
                           'planner_model': 'not a model'}})
        config = storage.settings()
        self.assertFalse(config['features']['web_search'])
        self.assertEqual(config['institution_names'], 'common_public')
        self.assertEqual(config['public_institutions_added'], ['Body One'])
        self.assertEqual(config['web_search']['max_searches_per_message'], 3)
        self.assertEqual(config['web_search']['pages_per_search'], 1)
        self.assertEqual(config['web_search']['engine'], 'brave')
        self.assertEqual(config['web_search']['cost_per_search_usd'], 0.005)
        self.assertEqual(config['web_search']['planner_model'], '')

    def test_engine_uses_the_saved_list(self):
        privacy = PrivacyEngine(WordAnalyzer())
        engine.apply_privacy_mode(privacy, {'institution_names': 'mask_all', 'public_institutions_removed': ['SEC']})
        self.assertEqual(privacy.institution_names, 'mask_all')
        self.assertNotIn('SEC', privacy.institutions)
        engine.apply_privacy_mode(privacy, {})
        self.assertEqual(privacy.institution_names, 'common_public')
        self.assertIn('SEC', privacy.institutions)

    def test_settings_api_stores_changes_to_the_defaults(self):
        import asyncio
        import app
        wanted = [n for n in public_institutions.DEFAULT_INSTITUTIONS if n != 'HKMA'] + ['Example Securities Board']
        result = asyncio.run(app.put_web_search_settings(app.WebSearchSettings(institutions=wanted, feature_enabled=True, max_searches_per_message=2)))
        saved = storage.settings()
        self.assertEqual(saved['public_institutions_removed'], ['HKMA'])
        self.assertEqual(saved['public_institutions_added'], ['Example Securities Board'])
        self.assertTrue(saved['features']['web_search'])
        self.assertEqual(saved['web_search']['max_searches_per_message'], 2)
        self.assertNotIn('HKMA', result['institutions'])
        self.assertEqual(set(result['engines']['brave']), {'label', 'available', 'detail'})  # status only, never the key


if __name__ == '__main__':
    unittest.main()
