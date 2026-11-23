"""The meeting extraction test harness, run only against a fake model. Nothing is paid and no real data is read."""
import asyncio
import json
import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import storage
from eval import run_meeting_eval as ev
from privacy_engine import PrivacyEngine
from test_streaming import FakeAnalyzer


def fake_model(calls):
    """A stand-in 'model' that reads the numbered transcript and reports what the generator planted."""
    async def complete(messages, model, *args, **kwargs):
        calls.append(messages)
        if messages[-1]['content'].startswith('Return only a concise meeting title'):
            return 'A title'
        lines = re.findall(r'^L(\d+): (?:\w+: )?(.*)$', messages[1]['content'], re.M)
        record = {'brief': 'Brief.', 'decisions': [], 'actions': [], 'open_questions': [], 'risks': [], 'key_figures': []}
        for number, text in lines:
            n = int(number)
            if 'that\'s decided' in text:
                record['decisions'].append({'text': text, 'lines': [n]})
            if m := re.search(r'^(.+?) will handle the (\w+) filing by (.+)\.$', text):
                record['actions'].append({'task': f'Handle the {m[2]} filing', 'owner': m[1], 'due': m[3], 'lines': [n]})
            if 'biggest risk' in text:
                record['risks'].append({'text': text, 'lines': [n]})
            if m := re.search(r'the (\w+) budget is (\$[\d,]+)', text):
                record['key_figures'].append({'figure': m[2], 'context': f'{m[1]} budget', 'lines': [n]})
            if 'who signs off' in text:
                record['open_questions'].append({'text': text, 'lines': [n]})
        return json.dumps(record)
    return complete


class MeetingEvalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'terms.txt'), ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)
        storage.write_json(storage.DATA / 'settings.json', {'model': 'test/model'})
        self.privacy = PrivacyEngine(FakeAnalyzer())

    def test_generator_is_deterministic_noisy_and_keywords_are_unique(self):
        first, second = ev.generate(10), ev.generate(10)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 10)
        keywords = [p['keyword'].lower() for m in first for s in ev.SECTIONS for p in m['planted'][s]]
        self.assertEqual(len(keywords), len(set(keywords)))
        text = '\n'.join(m['text'] for m in first)
        self.assertIn('[inaudible]', text)
        self.assertIn('<script>alert(1)</script>', text)  # HTML-like text is planted to prove it stays inert
        lines = text.splitlines()
        self.assertTrue(any(not re.match(r'^\w+: ', line) for line in lines))  # some lines have no speaker label
        for meeting in first:
            for plant in meeting['planted']['decisions'] + meeting['planted']['risks']:
                self.assertIn(plant['keyword'], meeting['text'])

    def test_a_faithful_model_scores_full_recall_and_precision_and_nothing_is_saved(self):
        calls = []
        meetings = ev.generate(10)
        results = asyncio.run(ev.run(meetings, self.privacy, fake_model(calls)))
        summary = ev.summarize(results)
        self.assertEqual((summary['meetings_run'], summary['meetings_failed'], summary['structured_ok']), (10, 0, 10))
        for section in ev.SECTIONS:
            self.assertEqual(summary['sections'][section]['recall_pct'], 100.0, section)
            self.assertEqual(summary['sections'][section]['precision_pct'], 100.0, section)
            self.assertEqual(summary['sections'][section]['unverified'], 0, section)
        actions = summary['sections']['actions']
        self.assertEqual((actions['owner_correct'], actions['due_correct']), (actions['found'], actions['found']))
        self.assertEqual(summary['sections']['key_figures']['figure_correct'], summary['sections']['key_figures']['found'])
        self.assertEqual(len(calls), 20)  # one analysis and one title per meeting, no retries
        self.assertFalse((storage.DATA / 'sessions').exists())  # the harness never saves sessions

    def test_scoring_notices_missing_wrong_and_unverified_items(self):
        meeting = ev.generate(1)[0]
        planted = meeting['planted']
        kw = planted['decisions'][0]['keyword']
        record = {'decisions': [{'text': f'about {kw}', 'lines': [1], 'verified': False}, {'text': 'something unplanted', 'lines': [1], 'verified': True}],
                  'actions': [], 'open_questions': [], 'risks': [], 'key_figures': []}
        scores = ev.score_meeting(record, planted)
        self.assertEqual((scores['decisions']['found'], scores['decisions']['matching'], scores['decisions']['output']), (1, 1, 2))
        self.assertEqual((scores['decisions']['unverified'], scores['decisions']['unverified_planted']), (1, 1))
        self.assertEqual(scores['actions']['found'], 0)

    def test_the_spend_cap_stops_the_run_before_it_would_be_exceeded(self):
        calls, spent = [], [Decimal('0')]
        inner = fake_model(calls)

        async def paid(messages, model, *args, **kwargs):
            spent[0] += Decimal('0.05')
            return await inner(messages, model, *args, **kwargs)
        guard = ev.SpendGuard(Decimal('0.10'), lambda: spent[0], lambda messages, max_tokens: Decimal('0.04'))
        results = asyncio.run(ev.run(ev.generate(5), self.privacy, paid, guard))
        self.assertTrue(guard.tripped)
        self.assertEqual(len(calls), 2)                   # one meeting (analysis + title), then stopped before the next call
        self.assertLessEqual(spent[0], Decimal('0.10'))   # never past the cap
        self.assertEqual(ev.summarize(results)['meetings_run'], 1)

    def test_the_report_names_problems_and_states_the_cost(self):
        report = {'date': '2026-11-23', 'model': 'x/y', 'meetings': 2, 'max_usd': '0.20', 'cost_usd': '0.0123', 'stopped': True,
                  'summary': ev.summarize([{'meeting': 'm01', 'structured': False, 'failure': 'bad JSON',
                                            'scores': ev.score_meeting({}, ev.generate(1)[0]['planted'])}]),
                  'results': [{'meeting': 'm01', 'structured': False, 'failure': 'bad JSON'}, {'meeting': 'm02', 'error': 'stopped'}]}
        text = ev.markdown(report)
        self.assertIn('US$0.0123', text)
        self.assertIn('stopped at the cap', text)
        self.assertIn('m01: bad JSON', text)


if __name__ == '__main__':
    unittest.main()
