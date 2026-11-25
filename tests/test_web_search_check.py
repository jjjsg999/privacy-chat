"""Query check: placeholders, sources, amounts, dates, labels, reruns, fail-closed. Offline; all names invented."""
import unittest

import web_search
from privacy_engine import parse_terms
from web_search import AMOUNT_RE, CheckError, check_query

TERMS = parse_terms('CLIENT: Quillmere Freight\nPROJECT: Bluefin')
MAP = {'CLIENT__2': 'Quillmere Freight', 'PERSON__1': 'Orla Venn', 'ORG__4': 'Tarrow Capital',
       'DATE_TIME__3': '2025', 'DATE_TIME__5': 'last Tuesday', 'LITERAL__6': 'ABC__1', 'PROJECT__7': 'Bluefin'}


def check(query, **kw):
    return check_query(query, dict(MAP), TERMS, **kw)


class AmountTests(unittest.TestCase):
    AMOUNTS = [  # (text, the amount that must be found)
        ('refinance a S$180m facility', 'S$180m'), ('US$2.5bn bond', 'US$2.5bn'), ('a $1,200,000 loan', '$1,200,000'),
        ('HK$ 30 million deal', 'HK$ 30 million'), ('€5m grant', '€5m'), ('£10k fee', '£10k'), ('¥300bn budget', '¥300bn'),
        ('SGD 180m term loan', 'SGD 180m'), ('180m SGD loan', '180m SGD'), ('USD1.2bn notes', 'USD1.2bn'),
        ('EUR 40,000 cap', 'EUR 40,000'), ('RMB 3 billion fund', 'RMB 3 billion'), ('200 million dollars raised', '200 million dollars'),
        ('a 2.5 bn USD round', '2.5 bn USD'), ('JPY 500mn loan', 'JPY 500mn'), ('aud 75k payment', 'aud 75k'),
        ('$5 price', '$5'), ('worth 12,500.50 EUR today', '12,500.50 EUR'), ('S$ 2.75 million', 'S$ 2.75 million'),
        ('raised 40 thousand pounds', '40 thousand pounds'), ('a HKD3bn facility', 'HKD3bn'),
    ]
    NOT_AMOUNTS = ['CET1 4.5% minimum', 'a 25bp hike', 'Basel III rules', 'results for 2025', '3 banks merged',
                   'USD rates outlook', 'S&P 500 index', 'Tier 1 capital', 'MAS Notice 637', 'a 10-year bond', 'page 12m']

    def test_amounts_are_found(self):
        for text, amount in self.AMOUNTS:
            with self.subTest(text=text):
                self.assertEqual([m[0] for m in AMOUNT_RE.finditer(text)], [amount])

    def test_non_amounts_are_left(self):
        for text in self.NOT_AMOUNTS:
            with self.subTest(text=text):
                self.assertEqual([m[0] for m in AMOUNT_RE.finditer(text)], [])

    def test_table_size(self):
        self.assertGreaterEqual(len(self.AMOUNTS) + len(self.NOT_AMOUNTS), 20)


class CheckTests(unittest.TestCase):
    def test_public_query_is_sent_as_written(self):
        result = check('MAS capital adequacy requirements corporate lending Singapore')
        self.assertEqual(result['kind'], 'public')
        self.assertEqual(result['sent_query'], 'MAS capital adequacy requirements corporate lending Singapore')
        self.assertIsNone(result['rerun_query'])
        self.assertEqual(result['removed'], [])

    def test_placeholders_and_amounts_make_it_generic(self):
        result = check('CLIENT__2 S$180m facility refinancing logistics sector')
        self.assertEqual(result['kind'], 'generic')
        self.assertEqual(result['sent_query'], 'facility refinancing logistics sector')
        # "Rerun unmasked" restores everything: the name and the amount, exactly as the planner wrote them.
        self.assertEqual(result['rerun_query'], 'Quillmere Freight S$180m facility refinancing logistics sector')
        self.assertEqual([(r['display'], r['source']) for r in result['removed']], [('Quillmere Freight', 'terms'), ('S$180m', 'amount')])
        self.assertEqual([n['token'] for n in result['rerun_names']], ['CLIENT__2'])
        self.assertEqual([e['display'] for e in result['rerun_extras']], ['S$180m'])

    def test_source_detection(self):
        result = check('ORG__4 credit rating bond issue')
        self.assertEqual(result['removed'][0]['source'], 'detector')
        self.assertEqual(result['removed'][0]['kind'], 'ORG')
        self.assertEqual(check('PROJECT__7 rollout plans')['removed'][0]['source'], 'terms')

    def test_unknown_and_literal_tokens_are_removed(self):
        result = check('PERSON__9 LITERAL__6 bank credit rating outlook')
        self.assertEqual([r['source'] for r in result['removed']], ['unknown', 'unknown'])
        self.assertEqual(result['sent_query'], 'bank credit rating outlook')
        self.assertIsNone(result['rerun_query'])

    def test_amounts_never_leave_in_an_ordinary_search_but_return_in_a_rerun(self):
        result = check('CLIENT__2 SGD 180m loan pricing banks')
        self.assertNotIn('180', result['sent_query'])
        self.assertIn('SGD 180m', result['rerun_query'])
        amounts_only = check('SGD 180m loan pricing banks')
        self.assertEqual(amounts_only['rerun_query'], 'SGD 180m loan pricing banks')  # an amount alone is worth a rerun

    def test_dates_removed_by_default(self):
        result = check('CLIENT__2 annual report DATE_TIME__3 results')
        self.assertNotIn('2025', result['sent_query'])
        self.assertIn('2025', result['rerun_query'])  # leftover date placeholders are restored by a rerun
        self.assertIn('date', [r['source'] for r in result['removed']])

    def test_public_values_may_appear_as_written(self):
        allowed = lambda token, value: value == 'Orla Venn'  # e.g. a name since treated as public
        result = check_query('orla venn interview latest', dict(MAP), TERMS, allowed=allowed)
        self.assertEqual((result['kind'], result['sent_query']), ('public', 'orla venn interview latest'))

    def test_years_setting_sends_bare_years_only(self):
        result = check('bank results DATE_TIME__3 DATE_TIME__5 outlook', dates='years')
        self.assertEqual(result['sent_query'], 'bank results 2025 outlook')
        self.assertEqual([r['display'] for r in result['removed']], ['last Tuesday'])
        self.assertEqual(check('bank results DATE_TIME__3 outlook', dates='years')['kind'], 'public')

    def test_skipped_when_too_little_is_left(self):
        result = check('CLIENT__2 news')
        self.assertEqual(result['kind'], 'skipped')
        self.assertEqual(result['rerun_query'], 'Quillmere Freight news')

    def test_terms_names_never(self):
        result = check('CLIENT__2 ORG__4 bond issue rating', terms_names='never')
        self.assertEqual(result['rerun_query'], 'Tarrow Capital bond issue rating')
        only_terms = check('CLIENT__2 bond issue rating', terms_names='never')
        self.assertIsNone(only_terms['rerun_query'])
        self.assertTrue(only_terms['rerun_withheld'])
        with_amount = check('CLIENT__2 S$5m bond issue rating', terms_names='never')
        self.assertEqual(with_amount['rerun_query'], 'S$5m bond issue rating')  # the name stays out, the amount returns

    def test_repeated_placeholder_is_listed_once(self):
        result = check('CLIENT__2 vs CLIENT__2 peers logistics')
        self.assertEqual(len(result['removed']), 1)

    def test_fail_closed(self):
        for query, token_map in [('', MAP), ('x' * 201, MAP), (None, MAP), ('bank news', None), ('bank news', {'A__1': 5})]:
            with self.subTest(query=str(query)[:20]):
                with self.assertRaises(CheckError):
                    check_query(query, token_map, TERMS)
        with self.assertRaises(CheckError):
            check_query('bank news', MAP, TERMS, dates='sometimes')

    def test_a_real_value_in_the_planner_text_stops_the_search(self):
        with self.assertRaises(CheckError):
            check('Quillmere Freight refinancing')          # a terms-list value written out
        with self.assertRaises(CheckError):
            check('orla venn biography interview')          # a detector value, any case
        with self.assertRaises(CheckError):
            check_query('bluefin rollout plans', {}, TERMS)  # a terms entry not yet in the map

    def test_meaningful_words(self):
        self.assertEqual(web_search.meaningful_words('the news about a bank'), ['bank'])


if __name__ == '__main__':
    unittest.main()
