"""Masking web text: terms list and the chat's token map only; detectors never run. All names invented."""
import unittest

from privacy_engine import PrivacyEngine, parse_terms


class NoDetector:
    calls = 0

    def analyze(self, text, language):
        NoDetector.calls += 1
        raise AssertionError('detectors must not run on web text')


class NoFilter:
    def detect(self, text):
        raise AssertionError('the Privacy Filter must not run on web text')


class MaskWebTextTests(unittest.TestCase):
    def setUp(self):
        self.terms = parse_terms('CLIENT: Quillmere Freight\nPROJECT: Bluefin')
        self.map = {'CLIENT__2': 'Quillmere Freight', 'PERSON__1': 'Orla Venn', 'LITERAL__3': 'ABC__1'}

    def mask(self, text, mode='standard'):
        privacy = PrivacyEngine(NoDetector(), mode=mode, privacy_filter=NoFilter())
        return privacy.mask_web_text(text, self.map, self.terms)

    def test_known_values_become_their_placeholders(self):
        out = self.mask('QUILLMERE FREIGHT hired orla venn as CFO.')
        self.assertEqual(out, 'CLIENT__2 hired PERSON__1 as CFO.')

    def test_new_terms_matches_get_tokens(self):
        out = self.mask('The Bluefin rollout is late.')
        self.assertNotIn('Bluefin', out)
        token = next(t for t, v in self.map.items() if v == 'Bluefin')
        self.assertTrue(token.startswith('PROJECT__'))
        self.assertIn(token, out)

    def test_public_names_institutions_and_places_are_left(self):
        text = 'Jane Public met the MAS and the Bank of England in Singapore and London.'
        self.assertEqual(self.mask(text), text)
        self.assertEqual(self.mask(text, mode='layered'), text)

    def test_placeholder_shaped_web_text_cannot_be_rehydrated_into_a_name(self):
        out = self.mask('A page that says CLIENT__2 and ABC__1 literally.')
        self.assertNotIn('CLIENT__2 and', out)  # reserved as a LITERAL token, so it restores to itself
        self.assertEqual(PrivacyEngine.rehydrate(out, self.map), 'A page that says CLIENT__2 and ABC__1 literally.')

    def test_word_boundaries(self):
        self.assertEqual(self.mask('Orla Venners is someone else'), 'Orla Venners is someone else')


if __name__ == '__main__':
    unittest.main()
