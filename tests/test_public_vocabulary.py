"""Dates pass through; topic glossary and the user's public names. Offline, fake detectors, invented names."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import engine
import public_topics
import storage
from privacy_engine import PrivacyEngine, parse_terms
from test_public_institutions import FakeFilter, WordAnalyzer


def mask(text, analyzer, terms='', **kw):
    token_map = {}
    return PrivacyEngine(analyzer, **kw).tokenize(text, token_map, parse_terms(terms)), token_map


class DateTests(unittest.TestCase):
    def test_dates_are_not_masked_in_either_mode(self):
        text = 'Call Orla Vennick this week, before 3 March 2026.'
        analyzer = WordAnalyzer('this week', '3 March 2026', kind='DATE_TIME')
        self.assertEqual(mask(text, analyzer)[0], text)
        layered = PrivacyEngine(analyzer, mode='layered', privacy_filter=FakeFilter())
        self.assertEqual(layered.tokenize(text, {}, []), text)

    def test_number_groups_labelled_as_dates_stay_masked(self):
        # spaCy labels account numbers as DATE_TIME; only date-shaped findings pass (found by the privacy evaluation).
        for value in ('6602 1187 4093', '0048-2217-9935', '8583-323-5739'):
            out, _ = mask(f'Wire it to account {value} today', WordAnalyzer(value, kind='DATE_TIME'))
            self.assertNotIn(value, out)
        for value in ('2026', '2026-11-05', '12/04/2026', '10:30', 'last Monday', 'Q3 2026'):
            out, _ = mask(f'Due {value} please', WordAnalyzer(value, kind='DATE_TIME'))
            self.assertIn(value, out)

    def test_a_date_span_holding_chinese_text_stays_masked(self):
        value = '陳宏偉。last Monday'
        out, _ = mask(f'副本給 {value} 之前完成', WordAnalyzer(value, kind='DATE_TIME'))
        self.assertNotIn('陳宏偉', out)

    def test_a_date_on_the_terms_list_is_still_masked(self):
        out, token_map = mask('The deal closes on 3 March 2026.', WordAnalyzer('3 March 2026', kind='DATE_TIME'), terms='DEADLINE: 3 March 2026')
        self.assertNotIn('3 March 2026', out)
        self.assertEqual(list(token_map), ['DEADLINE__1'])

    def test_mask_restores_old_behaviour_and_changes_the_fingerprint(self):
        out, token_map = mask('Meet this week.', WordAnalyzer('this week', kind='DATE_TIME'), date_names='mask')
        self.assertIn('DATE_TIME__1', out)
        privacy = PrivacyEngine(WordAnalyzer())
        before = privacy.fingerprint('')
        privacy.date_names = 'mask'
        self.assertNotEqual(privacy.fingerprint(''), before)
        with self.assertRaises(ValueError):
            privacy.date_names = 'sometimes'

    def test_web_text_keeps_dates_while_dates_pass(self):
        privacy = PrivacyEngine(WordAnalyzer())
        token_map = {'DATE_TIME__1': 'this week', 'PERSON__2': 'Orla Vennick'}
        self.assertEqual(privacy.mask_web_text('Orla Vennick spoke this week.', token_map, []), 'PERSON__2 spoke this week.')


class TopicTests(unittest.TestCase):
    def test_glossary_size_and_content(self):
        self.assertTrue(100 <= len(public_topics.DEFAULT_TOPICS) <= 200)
        for term in ('Digital Asset Custody', 'Payment Services Act', 'KYC', 'Basel III', 'Letter of Credit'):
            self.assertIn(term, public_topics.DEFAULT_TOPICS)

    def test_topic_passes_through(self):
        text = 'the latest on MAS regulation about Digital Asset Custody'
        self.assertEqual(mask(text, WordAnalyzer('Digital Asset Custody'))[0], text)

    def test_acronyms_are_case_sensitive(self):
        out, token_map = mask('Our kyc desk handles KYC checks', WordAnalyzer('kyc', 'KYC'))
        self.assertIn('KYC checks', out)
        self.assertIn('kyc', token_map.values())

    def test_terms_list_wins(self):
        out, _ = mask('Digital Asset Custody project', WordAnalyzer('Digital Asset Custody'), terms='PROJECT: Digital Asset Custody')
        self.assertNotIn('Digital Asset Custody', out)

    def test_mask_all_restores_masking(self):
        out, _ = mask('about Digital Asset Custody', WordAnalyzer('Digital Asset Custody'), topic_names='mask_all')
        self.assertNotIn('Digital Asset Custody', out)

    def test_layered_privacy_filter_spans_are_released_too(self):
        privacy = PrivacyEngine(WordAnalyzer(), mode='layered', privacy_filter=FakeFilter('Payment Services Act'))
        self.assertIn('Payment Services Act', privacy.tokenize('under the Payment Services Act', {}, []))


class PublicNameTests(unittest.TestCase):
    def test_public_names_pass_case_insensitively(self):
        out, _ = mask('what happened to jensen huang', WordAnalyzer('jensen huang', kind='PERSON'), public_names=['Jensen Huang'])
        self.assertEqual(out, 'what happened to jensen huang')

    def test_terms_list_wins_over_public_names(self):
        out, _ = mask('Ask Tamsin Rowe', WordAnalyzer('Tamsin Rowe', kind='PERSON'), terms='PERSON: Tamsin Rowe', public_names=['Tamsin Rowe'])
        self.assertNotIn('Tamsin Rowe', out)

    def test_adding_a_name_changes_the_fingerprint(self):
        privacy = PrivacyEngine(WordAnalyzer())
        before = privacy.fingerprint('')
        privacy.public_names = ['Jensen Huang']
        self.assertNotEqual(privacy.fingerprint(''), before)

    def test_web_text_leaves_a_public_name_readable(self):
        privacy = PrivacyEngine(WordAnalyzer(), public_names=['Jensen Huang'])
        self.assertEqual(privacy.mask_web_text('Jensen Huang spoke.', {'PERSON__1': 'Jensen Huang'}, []), 'Jensen Huang spoke.')


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'sensitive_terms.txt'), ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)

    def test_defaults_and_dates_in_queries_is_ignored(self):
        storage.write_json(storage.DATA / 'settings.json', {'model': 'a/b', 'web_search': {'dates_in_queries': 'years'}})
        config = storage.settings()
        self.assertEqual((config['date_names'], config['topic_names'], config['public_names']), ('pass', 'common_public', []))
        self.assertNotIn('dates_in_queries', config['web_search'])

    def test_api_saves_lists_and_the_engine_uses_them(self):
        wanted = [t for t in public_topics.DEFAULT_TOPICS if t != 'KYC'] + ['Tokenised Money Market Fund']
        asyncio.run(app.put_web_search_settings(app.WebSearchSettings(topics=wanted, date_names='mask', public_names=['Jensen Huang'])))
        view = asyncio.run(app.add_public_name(app.PublicName(name='  Nadia  Okafor ')))
        config = storage.settings()
        self.assertEqual(config['public_topics_removed'], ['KYC'])
        self.assertEqual(config['public_topics_added'], ['Tokenised Money Market Fund'])
        self.assertEqual(view['public_names'], ['Jensen Huang', 'Nadia Okafor'])
        privacy = PrivacyEngine(WordAnalyzer())
        engine.apply_privacy_mode(privacy, config)
        self.assertEqual(privacy.date_names, 'mask')
        self.assertNotIn('KYC', privacy.topics)
        self.assertEqual(privacy.public_names, ['Jensen Huang', 'Nadia Okafor'])


if __name__ == '__main__':
    unittest.main()
