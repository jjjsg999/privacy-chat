"""Mocked tests for the layered privacy filter. No model, no network."""
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import engine
import storage
from privacy_engine import (PRIVACY_FILTER_LABELS, PrivacyEngine, PrivacyFilter, PrivacyFilterUnavailable,
                            merge_spans, parse_terms)


def finding(start, end, kind, recognizer='EmailRecognizer'):
    return SimpleNamespace(start=start, end=end, score=.9, entity_type=kind,
                           recognition_metadata={'recognizer_name': recognizer})


class FakeAnalyzer:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = 0

    def analyze(self, text, language):
        self.calls += 1
        return self.results


class FakeFilter:
    def __init__(self, found=()):
        self.found = list(found)
        self.seen = []

    def detect(self, text):
        self.seen.append(text)
        return self.found


class MergeRuleTests(unittest.TestCase):
    def test_overlap_becomes_one_combined_span(self):
        merged = merge_spans([(0, 5, 'PERSON', 'spacy'), (3, 9, 'LOCATION', 'spacy')])
        self.assertEqual([(s[0], s[1]) for s in merged], [(0, 9)])

    def test_chain_of_overlaps_is_one_span(self):
        merged = merge_spans([(0, 4, 'A', 'spacy'), (3, 7, 'B', 'spacy'), (6, 10, 'C', 'spacy')])
        self.assertEqual([(s[0], s[1]) for s in merged], [(0, 10)])

    def test_touching_spans_stay_separate(self):
        merged = merge_spans([(0, 3, 'PERSON', 'spacy'), (3, 6, 'PERSON', 'spacy')])
        self.assertEqual(len(merged), 2)

    def test_priority_terms_over_pattern_over_filter_over_spacy(self):
        order = ['terms', 'pattern', 'privacy_filter', 'spacy']
        for i, winner in enumerate(order):
            members = [(0, 5, f'K_{src}', src) for src in order[i:]]
            self.assertEqual(merge_spans(members)[0][2], f'K_{winner}')

    def test_priority_beats_length_but_union_keeps_full_extent(self):
        merged = merge_spans([(0, 12, 'LOCATION', 'spacy'), (4, 8, 'PERSON', 'privacy_filter')])
        self.assertEqual(merged, [(0, 12, 'PERSON', 'privacy_filter')])

    def test_equal_priority_longer_span_decides_kind(self):
        merged = merge_spans([(0, 3, 'SHORT', 'spacy'), (1, 9, 'LONG', 'spacy')])
        self.assertEqual(merged[0][2], 'LONG')

    def test_empty_spans_dropped(self):
        self.assertEqual(merge_spans([(4, 4, 'X', 'spacy')]), [])


class LabelMappingTests(unittest.TestCase):
    def test_labels_map_to_existing_token_names(self):
        self.assertEqual(PRIVACY_FILTER_LABELS['private_person'], 'PERSON')
        self.assertEqual(PRIVACY_FILTER_LABELS['private_email'], 'EMAIL_ADDRESS')
        self.assertEqual(PRIVACY_FILTER_LABELS['private_phone'], 'PHONE_NUMBER')
        self.assertEqual(PRIVACY_FILTER_LABELS['private_url'], 'URL')

    def test_new_kinds_only_where_none_existed(self):
        self.assertEqual({PRIVACY_FILTER_LABELS[k] for k in ('account_number', 'private_address', 'secret')},
                         {'ACCOUNT_NUMBER', 'ADDRESS', 'SECRET'})

    def test_private_date_is_not_mapped(self):
        self.assertNotIn('private_date', PRIVACY_FILTER_LABELS)

    def test_filter_labels_become_tokens_and_dates_are_ignored(self):
        text = 'Mina Park lives at 12 Elm Row on 5 May'
        fake = FakeFilter([(0, 9, 'private_person'), (19, 29, 'private_address'), (33, 38, 'private_date')])
        privacy = PrivacyEngine(FakeAnalyzer(), mode='layered', privacy_filter=fake)
        mapping = {}
        masked = privacy.tokenize(text, mapping, [])
        self.assertEqual(masked, 'PERSON__2 lives at ADDRESS__1 on 5 May')
        self.assertEqual(sorted(mapping.values()), ['12 Elm Row', 'Mina Park'])
        self.assertEqual(privacy.rehydrate(masked, mapping), text)


class LayeredBehaviourTests(unittest.TestCase):
    def test_terms_beat_filter_and_value_never_partly_masked(self):
        text = 'Ask Jo Lin about Project Falcon today'
        found = [(17, 33, 'private_person')]  # filter wrongly spans "Project Falcon"
        analyzer = FakeAnalyzer([finding(21, 33, 'LOCATION', 'SpacyRecognizer')])
        privacy = PrivacyEngine(analyzer, mode='layered', privacy_filter=FakeFilter(found))
        spans = privacy.final_spans(text, parse_terms('DEAL: Project Falcon'))
        self.assertEqual(len(spans), 1)
        self.assertEqual((spans[0][2], spans[0][3]), ('DEAL', 'terms'))
        self.assertLessEqual(spans[0][0], 17)
        self.assertGreaterEqual(spans[0][1], 31)
        masked = privacy.tokenize(text, {}, parse_terms('DEAL: Project Falcon'))
        self.assertNotIn('Falcon', masked)
        self.assertNotIn('Project', masked)

    def test_presidio_partitioned_into_pattern_and_spacy(self):
        analyzer = FakeAnalyzer([finding(0, 5, 'EMAIL_ADDRESS'), finding(10, 14, 'PERSON', 'SpacyRecognizer')])
        privacy = PrivacyEngine(analyzer, mode='layered', privacy_filter=FakeFilter())
        spans = privacy.final_spans('a@b.c and Anna', [])
        self.assertEqual({s[3] for s in spans}, {'pattern', 'spacy'})
        self.assertEqual(analyzer.calls, 1)

    def test_pattern_beats_filter_beats_spacy(self):
        analyzer = FakeAnalyzer([finding(0, 6, 'PHONE_NUMBER'), finding(0, 6, 'LOCATION', 'SpacyRecognizer')])
        privacy = PrivacyEngine(analyzer, mode='layered', privacy_filter=FakeFilter([(0, 6, 'account_number')]))
        self.assertEqual(privacy.final_spans('123456', [])[0][2:], ('PHONE_NUMBER', 'pattern'))
        privacy = PrivacyEngine(FakeAnalyzer([finding(0, 6, 'LOCATION', 'SpacyRecognizer')]), mode='layered',
                                privacy_filter=FakeFilter([(0, 6, 'account_number')]))
        self.assertEqual(privacy.final_spans('123456', [])[0][2:], ('ACCOUNT_NUMBER', 'privacy_filter'))

    def test_one_token_per_value_and_existing_maps_keep_working(self):
        privacy = PrivacyEngine(FakeAnalyzer(), mode='layered', privacy_filter=FakeFilter([(0, 4, 'private_person'), (9, 13, 'private_person')]))
        mapping = {'PERSON__1': 'Anna'}  # token map from an earlier (standard) turn
        masked = privacy.tokenize('Anna met Anna', mapping, [])
        self.assertEqual(masked, 'PERSON__1 met PERSON__1')
        self.assertEqual(privacy.rehydrate(masked, mapping), 'Anna met Anna')

    def test_placeholder_text_is_protected(self):
        privacy = PrivacyEngine(FakeAnalyzer(), mode='layered', privacy_filter=FakeFilter([(0, 9, 'private_person')]))
        mapping = {'PERSON__1': 'Anna'}
        masked = privacy.tokenize('PERSON__1 hi', mapping, [])
        self.assertEqual(masked, 'LITERAL__2 hi')  # reserved literal text, as in standard mode
        self.assertEqual(privacy.rehydrate(masked, mapping), 'PERSON__1 hi')


class FailClosedTests(unittest.TestCase):
    def test_missing_model_raises_clear_error(self):
        with self.assertRaises(PrivacyFilterUnavailable) as caught:
            PrivacyFilter('Z:/definitely/not/here')
        self.assertIn('Nothing was sent', str(caught.exception))

    def test_layered_without_model_refuses_instead_of_falling_back(self):
        privacy = PrivacyEngine(FakeAnalyzer([finding(0, 4, 'PERSON', 'SpacyRecognizer')]), mode='layered')
        with patch('privacy_engine.load_privacy_filter', side_effect=PrivacyFilterUnavailable('no model')):
            with self.assertRaises(PrivacyFilterUnavailable):
                privacy.tokenize('Anna says hi', {}, [])

    def test_filter_runtime_error_propagates(self):
        class Broken:
            def detect(self, text):
                raise RuntimeError('boom')
        privacy = PrivacyEngine(FakeAnalyzer(), mode='layered', privacy_filter=Broken())
        with self.assertRaises(RuntimeError):
            privacy.tokenize('Anna', {}, [])

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            PrivacyEngine(FakeAnalyzer(), mode='layred')


class StandardUnchangedTests(unittest.TestCase):
    def test_default_mode_is_standard_and_never_touches_filter(self):
        class Forbidden:
            def detect(self, text):
                raise AssertionError('filter must not run in standard mode')
        analyzer = FakeAnalyzer([finding(0, 4, 'PERSON', 'SpacyRecognizer'), finding(2, 6, 'LOCATION', 'SpacyRecognizer')])
        privacy = PrivacyEngine(analyzer, privacy_filter=Forbidden())
        self.assertEqual(privacy.mode, 'standard')
        mapping = {}
        self.assertEqual(privacy.tokenize('abcdefg Acme', mapping, parse_terms('CLIENT: Acme')), 'PERSON__2g CLIENT__1')
        self.assertEqual(mapping, {'CLIENT__1': 'Acme', 'PERSON__2': 'abcdef'})

    def test_settings_default_is_standard(self):
        with patch.object(storage, 'DATA', storage.ROOT / 'data_does_not_exist'), \
             patch.object(storage, 'TERMS', storage.ROOT / 'terms_does_not_exist.txt'):
            self.assertEqual(storage.settings()['privacy_detectors'], 'standard')

    def test_engine_passes_setting_through(self):
        privacy = PrivacyEngine(FakeAnalyzer())
        engine.apply_privacy_mode(privacy, {'privacy_detectors': 'layered'})
        self.assertEqual(privacy.mode, 'layered')
        engine.apply_privacy_mode(privacy, {})
        self.assertEqual(privacy.mode, 'standard')



class CjkBoundaryTests(unittest.TestCase):
    """Chinese text has no spaces: placeholders and terms glued to it must still work."""

    def test_rehydrate_placeholder_next_to_cjk(self):
        self.assertEqual(PrivacyEngine.rehydrate('請PERSON__3跟 CLIENT__2開會', {'PERSON__3': '蔡', 'CLIENT__2': 'X'}),
                         '請蔡跟 X開會')

    def test_latin_boundaries_unchanged(self):
        mapping = {'PERSON__1': 'Anna'}
        self.assertEqual(PrivacyEngine.rehydrate('xPERSON__1 PERSON__1x éPERSON__1', mapping), 'xPERSON__1 PERSON__1x éPERSON__1')

    def test_terms_match_next_to_cjk_and_chinese_terms_match_inside_text(self):
        privacy = PrivacyEngine(FakeAnalyzer())
        mapping = {}
        terms = parse_terms('CLIENT: Acme\nPERSON: 陳偉倫')
        masked = privacy.tokenize('跟Acme開會，陳偉倫說', mapping, terms)
        self.assertNotIn('Acme', masked)
        self.assertNotIn('陳偉倫', masked)
        self.assertEqual(privacy.rehydrate(masked, mapping), '跟Acme開會，陳偉倫說')

    def test_terms_still_need_latin_word_boundaries(self):
        privacy = PrivacyEngine(FakeAnalyzer())
        self.assertEqual(privacy.tokenize('Acmes xAcme', {}, parse_terms('CLIENT: Acme')), 'Acmes xAcme')

    def test_layered_terms_next_to_cjk(self):
        privacy = PrivacyEngine(FakeAnalyzer(), mode='layered', privacy_filter=FakeFilter())
        masked = privacy.tokenize('跟Acme開會', {}, parse_terms('CLIENT: Acme'))
        self.assertNotIn('Acme', masked)

    def test_stream_rehydrator_with_cjk_split_across_chunks(self):
        from privacy_engine import StreamRehydrator
        mapping = {'PERSON__3': '蔡芬宏'}
        for chunks in (['請PERSON_', '_3跟你說'], ['請PERSON__3', '跟你說'], ['請', 'PERSON__3跟', '你說']):
            stream = StreamRehydrator(mapping)
            out = ''.join(stream.feed(c) for c in chunks) + stream.flush()
            self.assertEqual(out, '請蔡芬宏跟你說')


class PlaceNameTests(unittest.TestCase):
    def locations(self, text, words):
        return FakeAnalyzer([finding(text.index(w), text.index(w) + len(w), 'LOCATION', 'SpacyRecognizer') for w in words])

    def test_countries_regions_and_major_cities_pass_by_default(self):
        text = 'Flights from Paris to the Netherlands, then Asia and Germany.'
        privacy = PrivacyEngine(self.locations(text, ['Paris', 'Netherlands', 'Asia', 'Germany']))
        self.assertEqual(privacy.place_names, 'common_public')
        self.assertEqual(privacy.tokenize(text, {}, []), text)

    def test_small_places_and_addresses_still_masked(self):
        text = 'We met in Kleinbach near Oakfield Village.'
        privacy = PrivacyEngine(self.locations(text, ['Kleinbach', 'Oakfield Village']))
        masked = privacy.tokenize(text, {}, [])
        self.assertNotIn('Kleinbach', masked)
        self.assertNotIn('Oakfield', masked)

    def test_mask_all_masks_everything(self):
        text = 'Paris and Germany'
        privacy = PrivacyEngine(self.locations(text, ['Paris', 'Germany']), place_names='mask_all')
        masked = privacy.tokenize(text, {}, [])
        self.assertNotIn('Paris', masked)
        self.assertNotIn('Germany', masked)

    def test_layered_mode_same_rule_and_terms_still_win(self):
        text = 'Paris office, Germany, Kleinbach'
        analyzer = self.locations(text, ['Paris', 'Germany', 'Kleinbach'])
        privacy = PrivacyEngine(analyzer, mode='layered', privacy_filter=FakeFilter())
        masked = privacy.tokenize(text, {}, parse_terms('SENSITIVE: Germany'))
        self.assertIn('Paris', masked)
        self.assertNotIn('Germany', masked)  # a user term beats the public-place pass
        self.assertNotIn('Kleinbach', masked)

    def test_privacy_filter_address_is_not_a_public_place(self):
        text = 'Send it to 12 Paris Road'
        privacy = PrivacyEngine(self.locations(text, ['Paris']), mode='layered',
                                privacy_filter=FakeFilter([(11, 24, 'private_address')]))
        self.assertNotIn('Paris', privacy.tokenize(text, {}, []))

    def test_matching_is_case_and_article_insensitive_and_exact(self):
        from public_places import is_public_place
        for yes in ('paris', 'THE Netherlands', ' Hong Kong ', 'U.S.', 'Southeast Asia'):
            self.assertTrue(is_public_place(yes), yes)
        for no in ('Paris Road', 'Kleinbach', 'Acme Tower', ''):
            self.assertFalse(is_public_place(no), no)

    def test_invalid_setting_rejected_and_setting_passed_through(self):
        with self.assertRaises(ValueError):
            PrivacyEngine(FakeAnalyzer(), place_names='some')
        privacy = PrivacyEngine(FakeAnalyzer())
        engine.apply_privacy_mode(privacy, {'place_names': 'mask_all'})
        self.assertEqual(privacy.place_names, 'mask_all')
        engine.apply_privacy_mode(privacy, {})
        self.assertEqual(privacy.place_names, 'common_public')


if __name__ == '__main__':
    unittest.main()
