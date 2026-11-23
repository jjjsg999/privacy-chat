"""Structured meeting record, line numbers, verification, tracker, Telegram text. Invented transcripts; the model is mocked."""
import asyncio
import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import engine
import meeting_record as mr
import storage
from privacy_engine import PrivacyEngine
from test_streaming import FakeAnalyzer

LINES = ['Maya Okafor: we ship the pilot on 12 December.', 'Tom: budget is capped at $4,500 for now.',
         'Maya Okafor: Tom will send the vendor list by Friday.', 'Li Wei: 我們決定改用新供應商。', 'Tom: risk is the vendor delay.']


def record_json(**overrides):
    data = {'brief': 'The team agreed to ship.', 'decisions': [{'text': 'Ship the pilot', 'lines': [1]}],
            'actions': [{'task': 'Send the vendor list', 'owner': 'Tom', 'due': 'Friday', 'lines': [3]}],
            'open_questions': [{'text': 'Who pays?', 'lines': [2]}], 'risks': [{'text': 'Vendor delay', 'lines': [5]}],
            'key_figures': [{'figure': '$4,500', 'context': 'budget cap', 'lines': [2]}]}
    data.update(overrides)
    return json.dumps(data)


def finalize(raw=None, lines=LINES, restore=lambda s: s):
    return mr.finalize(mr.parse_record(raw or record_json()), lines, restore, model='m', at='2026-11-23T00:00:00+00:00')


class SchemaTests(unittest.TestCase):
    def test_valid_record_and_defaults(self):
        record = mr.parse_record(record_json())
        self.assertEqual(record.actions[0].owner, 'Tom')
        minimal = mr.parse_record('{"brief": "Only a brief"}')
        self.assertEqual((minimal.decisions, minimal.key_figures), ([], []))

    def test_fenced_or_wrapped_json_is_accepted(self):
        self.assertEqual(mr.parse_record('```json\n' + record_json() + '\n```').brief, 'The team agreed to ship.')
        self.assertEqual(mr.parse_record('Here you go: ' + record_json() + ' Thanks').brief, 'The team agreed to ship.')

    def test_invalid_records_raise_with_a_reason(self):
        for raw, expect in [('not json at all', 'not valid JSON'), ('[1, 2]', 'one JSON object'), ('{"decisions": []}', 'brief'),
                            (record_json(actions=[{'owner': 'Tom', 'lines': [1]}]), 'actions.0.task'),
                            (record_json(decisions=[{'text': 'x', 'lines': 'one'}]), 'lines')]:
            with self.assertRaises(mr.RecordError) as caught:
                mr.parse_record(raw)
            self.assertIn(expect, str(caught.exception))

    def test_line_references_may_be_numbers_or_l_numbers_and_blank_owner_is_none(self):
        record = mr.parse_record(record_json(decisions=[{'text': 'x', 'lines': ['L2', 4]}],
                                             actions=[{'task': 't', 'owner': ' ', 'due': '', 'lines': [1]}]))
        self.assertEqual(record.decisions[0].lines, [2, 4])
        self.assertEqual((record.actions[0].owner, record.actions[0].due), (None, None))

    def test_ids_are_assigned_locally_and_model_ids_ignored(self):
        data = json.loads(record_json())
        data['decisions'][0]['id'] = 'Z99'
        built = finalize(json.dumps(data))
        self.assertEqual([built[s][0]['id'] for s, _ in mr.SECTIONS], ['D1', 'A1', 'Q1', 'R1', 'F1'])


class VerificationTests(unittest.TestCase):
    def test_a_fully_supported_record_is_verified(self):
        built = finalize()
        self.assertTrue(all(i['verified'] and i['reason'] is None for s, _ in mr.SECTIONS for i in built[s]))

    def test_missing_line_numbers_are_flagged_and_items_kept(self):
        built = finalize(record_json(decisions=[{'text': 'Ghost', 'lines': [99]}, {'text': 'Nothing cited', 'lines': []}]))
        first, second = built['decisions']
        self.assertFalse(first['verified'])
        self.assertIn('L99', first['reason'])
        self.assertIn('does not exist', first['reason'])
        self.assertEqual(second['reason'], 'no line cited')
        self.assertEqual(len(built['decisions']), 2)

    def test_owner_rules(self):
        action = lambda owner, lines: finalize(record_json(actions=[{'task': 't', 'owner': owner, 'lines': lines}]))['actions'][0]
        self.assertTrue(action('Maya Okafor', [1])['verified'])
        self.assertTrue(action('maya', [1])['verified'])              # part of the name, any case
        self.assertTrue(action('Dr. OKAFOR', [1])['verified'])
        self.assertFalse(action('M', [1])['verified'])                 # one character is not enough
        self.assertEqual(action('Priya Rao', [1])['reason'], 'owner not found in cited lines')
        self.assertFalse(action('Maya', [2])['verified'])              # named in another line, not the cited one
        self.assertTrue(action(None, [1])['verified'])                 # no owner, nothing to check

    def test_cjk_owner_needs_the_full_name(self):
        action = lambda owner: finalize(record_json(actions=[{'task': 't', 'owner': owner, 'lines': [4]}]), lines=LINES + [''])['actions'][0]
        lines = ['x', 'x', 'x', 'Li Wei: 王小明 同意了。']
        verdict = lambda owner: finalize(record_json(actions=[{'task': 't', 'owner': owner, 'lines': [4]}]), lines=lines)['actions'][0]['verified']
        self.assertTrue(verdict('王小明'))
        self.assertFalse(verdict('王小華'))
        self.assertFalse(verdict('王大明'))

    def test_figure_rules_ignore_commas_and_spaces(self):
        figure = lambda text, lines: finalize(record_json(key_figures=[{'figure': text, 'context': 'c', 'lines': lines}]))['key_figures'][0]
        self.assertTrue(figure('$4,500', [2])['verified'])
        self.assertTrue(figure('4 500', [2])['verified'])
        self.assertTrue(figure('4500 dollars', [2])['verified'])
        self.assertIn('not found', figure('$9,900', [2])['reason'])
        self.assertFalse(figure('$4,500', [1])['verified'])             # right figure, wrong line
        self.assertEqual(figure('a lot', [2])['reason'], 'figure has no digits to check')

    def test_unverified_count(self):
        built = finalize(record_json(decisions=[{'text': 'x', 'lines': [42]}]))
        self.assertEqual(mr.unverified_count(built), 1)


class RestoreTests(unittest.TestCase):
    def test_placeholders_are_restored_in_every_string_field(self):
        raw = json.dumps({'brief': 'PERSON__1 leads.', 'decisions': [{'text': 'PERSON__1 decides', 'lines': [1]}],
                          'actions': [{'task': 'PERSON__1 sends', 'owner': 'PERSON__1', 'due': 'by PERSON__1', 'lines': [1]}],
                          'open_questions': [{'text': 'PERSON__1?', 'lines': [1]}], 'risks': [{'text': 'PERSON__1 risk', 'lines': [1]}],
                          'key_figures': [{'figure': 'PERSON__1 4', 'context': 'PERSON__1 ctx', 'lines': [1]}]})
        built = finalize(raw, restore=lambda s: s.replace('PERSON__1', 'Maya'))
        self.assertNotIn('PERSON__1', json.dumps(built))
        self.assertEqual(built['actions'][0]['owner'], 'Maya')
        self.assertEqual(built['key_figures'][0]['context'], 'Maya ctx')
        self.assertTrue(built['actions'][0]['verified'])  # the owner is verified after restoring, against real lines


class LineNumberTests(unittest.TestCase):
    def test_clean_lines_only_drops_blanks_and_consecutive_duplicates(self):
        text = 'a b\n\n  \na b\nx\nx\nx\n a b \n'
        self.assertEqual(mr.clean_lines(text), ['a b', 'x', 'a b'])  # the later 'a b' is kept: not consecutive
        self.assertEqual(mr.clean_lines('Zoë  said   hi'), ['Zoë  said   hi'])  # words and inner spacing untouched

    def test_numbered(self):
        self.assertEqual(mr.numbered(['a', 'b']), 'L1: a\nL2: b')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'terms.txt'), ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)

    def privacy_crossing_lines(self):
        def analyze(text, language):
            at = text.find('Anna\nSmith')
            return [SimpleNamespace(start=at, end=at + 10, score=.9, entity_type='PERSON')] if at >= 0 else []
        return PrivacyEngine(SimpleNamespace(analyze=analyze))

    def test_line_n_of_the_masked_text_is_line_n_of_the_transcript(self):
        privacy = PrivacyEngine(FakeAnalyzer())
        storage.atomic_write(storage.TERMS, 'CLIENT: Acme Corp')
        lines = ['Acme Corp: hello', 'Tom: ok', 'Acme Corp and Acme Corp again', 'end']
        session = {'token_map': {}}
        parts = engine.masked_transcript_lines(session, privacy, storage.settings(), lines)
        self.assertEqual(len(parts), len(lines))
        self.assertTrue(all('Acme' not in p for p in parts))
        self.assertEqual(parts[1], 'Tom: ok')
        self.assertIn('transcript_masked', session)

    def test_a_masked_span_across_a_line_break_cannot_shift_the_numbers(self):
        privacy = self.privacy_crossing_lines()
        lines = ['intro', 'Anna', 'Smith joined', 'closing']
        parts = engine.masked_transcript_lines({'token_map': {}}, privacy, storage.settings(), lines)
        self.assertEqual(len(parts), 4)  # masked line by line instead
        self.assertEqual(parts[0], 'intro')
        self.assertEqual(parts[3], 'closing')

    def test_the_cache_is_reused_and_invalidated_by_a_new_term(self):
        privacy = PrivacyEngine(FakeAnalyzer())
        session = {'token_map': {}}
        lines = ['Zorblax Corp is late', 'ok']
        engine.masked_transcript_lines(session, privacy, storage.settings(), lines)
        with patch.object(privacy, 'tokenize', side_effect=AssertionError('cached text must not be masked again')):
            self.assertEqual(engine.masked_transcript_lines(session, privacy, storage.settings(), lines)[0], 'Zorblax Corp is late')
        storage.atomic_write(storage.TERMS, 'CLIENT: Zorblax Corp')
        self.assertNotIn('Zorblax', ''.join(engine.masked_transcript_lines(session, privacy, storage.settings(), lines)))


class AnalysisFlowTests(unittest.TestCase):
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
        self.calls = []

    def run_analysis(self, replies, transcript='Maya Okafor: ship it.\nTom: ok\n\nTom: ok\nMaya Okafor: send the list Friday.'):
        queue = list(replies)

        async def complete(messages, model, *args, **kwargs):
            self.calls.append({'messages': messages, 'kwargs': kwargs})
            reply = queue.pop(0)
            return reply(messages) if callable(reply) else reply
        session = storage.new_session('telegram')
        session['session_type'] = 'meeting'
        with patch.object(engine, 'complete', complete):
            return asyncio.run(engine.analyze_meeting(session, transcript, self.privacy))

    def test_one_call_returns_the_record_and_the_numbered_masked_transcript_is_sent(self):
        record = json.dumps({'brief': 'B', 'actions': [{'task': 'Send the list', 'owner': 'Maya', 'due': 'Friday', 'lines': [3]}]})
        result = self.run_analysis([record, 'Pilot kickoff'])
        sent = self.calls[0]['messages'][1]['content']
        self.assertIn('L1: Maya Okafor: ship it.', sent)
        self.assertIn('L3: Maya Okafor: send the list Friday.', sent)  # the duplicate "Tom: ok" line was dropped
        intelligence = result['intelligence']
        self.assertEqual((intelligence['schema'], intelligence['structured'], intelligence['model']), (1, True, 'test/model'))
        self.assertTrue(intelligence['actions'][0]['verified'])
        self.assertEqual(result['messages'][1]['content'], 'B')
        self.assertEqual(result['transcript_lines'][2], 'Maya Okafor: send the list Friday.')
        self.assertEqual(len(self.calls), 2)  # the record, and the title

    def test_an_invalid_reply_is_retried_once_with_the_error(self):
        result = self.run_analysis(['{"decisions": []}', record_json(), 'Title'])
        retry = self.calls[1]['messages']
        self.assertEqual(retry[-2]['role'], 'assistant')
        self.assertIn('brief', retry[-1]['content'])
        self.assertTrue(result['intelligence']['structured'])
        self.assertEqual(len(self.calls), 3)

    def test_two_failures_keep_the_brief_as_prose_with_the_reason(self):
        result = self.run_analysis(['A plain prose brief.', '{"decisions": []}', 'Title'])
        intelligence = result['intelligence']
        self.assertFalse(intelligence['structured'])
        self.assertIn('brief', intelligence['failure'])
        self.assertEqual(intelligence['actions'], [])
        self.assertEqual(result['messages'][1]['content'], '{"decisions": []}')  # the last reply, as prose

    def test_a_failure_with_a_brief_inside_keeps_just_the_brief(self):
        result = self.run_analysis([json.dumps({'brief': 'Good brief', 'actions': [{'owner': 'x'}]})] * 2 + ['Title'])
        self.assertFalse(result['intelligence']['structured'])
        self.assertEqual(result['intelligence']['brief'], 'Good brief')

    def test_a_failed_retry_call_still_saves_the_prose(self):
        async def complete(messages, model, *args, **kwargs):
            if len(self.calls) == 0:
                self.calls.append(1)
                return 'prose only'
            raise engine.ChatError('Monthly cost ceiling')
        session = storage.new_session('telegram')
        with patch.object(engine, 'complete', complete):
            result = asyncio.run(engine.analyze_meeting(session, 'Line one', self.privacy))
        self.assertFalse(result['intelligence']['structured'])
        self.assertIn('retry could not be sent', result['intelligence']['failure'])

    def test_json_mode_is_requested_only_when_the_model_supports_it(self):
        self.run_analysis([record_json(), 'T'])
        self.assertNotIn('response_format', self.calls[0]['kwargs'])
        storage.write_json(storage.DATA / engine.FEATURES_FILE, {'json_mode': ['test/model']})
        self.calls.clear()
        self.run_analysis([record_json(), 'T'])
        self.assertEqual(self.calls[0]['kwargs'], {'response_format': {'type': 'json_object'}})

    def test_placeholders_in_the_reply_are_restored_locally(self):
        storage.atomic_write(storage.TERMS, 'PERSON: Maya Okafor')
        def record(messages):  # the model echoes the placeholder it was given for the name
            token = re.search(r'PERSON__\d+', messages[1]['content'])[0]
            return json.dumps({'brief': token + ' leads.', 'actions': [{'task': token + ' sends', 'owner': token, 'lines': [1]}]})
        result = self.run_analysis([record, 'T'])
        self.assertNotIn('Maya', json.dumps(self.calls[0]['messages']))  # masked on the way out
        self.assertEqual(result['intelligence']['actions'][0]['owner'], 'Maya Okafor')  # restored on the way in
        self.assertTrue(result['intelligence']['actions'][0]['verified'])

    def test_old_prose_sessions_still_display_and_answer_questions(self):
        old = storage.new_session('telegram')
        old.update(session_type='meeting', transcript='Maya Okafor: ship it.\nTom: ok', intelligence='An old prose analysis.',
                   mode='meeting_ready', messages=[{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'An old prose analysis.'}])
        storage.save_session(old)
        self.assertEqual([s['id'] for s in storage.list_sessions()], [old['id']])
        public = storage.public_session(old)
        self.assertEqual(public['intelligence'], 'An old prose analysis.')
        self.assertEqual(public['transcript_lines'], ['Maya Okafor: ship it.', 'Tom: ok'])

        async def complete(messages, model, *args, **kwargs):
            self.calls.append(messages)
            return 'Answer [L1]'
        with patch.object(engine, 'complete', complete):
            asyncio.run(engine.ask_meeting(old, 'What now?', self.privacy))
        self.assertIn('L2: Tom: ok', self.calls[0][1]['content'])
        self.assertIn('An old prose analysis.', self.calls[0][2]['content'])
        self.assertEqual(storage.load_session(old['id'])['messages'][-1]['content'], 'Answer [L1]')

    def test_questions_on_a_structured_record_send_its_brief(self):
        result = self.run_analysis([record_json(brief='The structured brief'), 'T'])
        session = storage.load_session(result['id'])
        self.calls.clear()

        async def complete(messages, model, *args, **kwargs):
            self.calls.append(messages)
            return 'ok'
        with patch.object(engine, 'complete', complete):
            asyncio.run(engine.ask_meeting(session, 'Q?', self.privacy))
        self.assertEqual(self.calls[0][2]['content'], 'The structured brief')


class QaReferenceTests(unittest.TestCase):
    def test_valid_and_invalid_references(self):
        found = mr.find_refs('See [L2] and [L12][L99] and [L0], not [L2x] or L3.', 12)
        self.assertEqual([(n, valid) for _, _, n, valid in found], [(2, True), (12, True), (99, False), (0, False)])
        self.assertEqual(mr.find_refs('no refs', 5), [])


class DueDateTests(unittest.TestCase):
    ref = date(2026, 11, 23)

    def test_common_formats_parse(self):
        for text, expect in [('2026-12-05', date(2026, 12, 5)), ('5 Dec 2026', date(2026, 12, 5)), ('December 5, 2026', date(2026, 12, 5)),
                             ('Dec 5th', date(2026, 12, 5)), ('5/12/2026', date(2026, 12, 5)), ('Friday, 5 December', date(2026, 12, 5)),
                             ('3 Jan', date(2027, 1, 3)), ('Sept 9 2026', date(2026, 9, 9))]:
            self.assertEqual(mr.parse_due(text, self.ref), expect, text)

    def test_unparseable_stays_none(self):
        for text in ['next Friday', 'ASAP', 'end of Q3', '', None, '31 Feb 2026']:
            self.assertIsNone(mr.parse_due(text, self.ref), text)


class TrackerTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'terms.txt'), ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)
        import app
        change = patch.object(app, 'PrivacyEngine', return_value=PrivacyEngine(FakeAnalyzer()))
        change.start()
        self.addCleanup(change.stop)
        app.chat_lock = asyncio.Lock()
        from fastapi.testclient import TestClient
        self.client = TestClient(app.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.first = self.meeting('Budget review', '2026-11-01T09:00:00+00:00', [('Send list', 'Tom', '5 Dec 2026'), ('Book room', 'Maya Okafor', 'next Friday')])
        self.second = self.meeting('Vendor call', '2026-11-10T09:00:00+00:00', [('Sign contract', 'Tom', '2026-12-01'), ('Call back', None, None)])

    def meeting(self, title, created, actions, **extra):
        raw = record_json(actions=[{'task': t, 'owner': o, 'due': d, 'lines': [1]} for t, o, d in actions])
        session = storage.new_session()
        session.update(title=title, created_at=created, session_type='meeting', transcript='\n'.join(LINES), mode='meeting_ready',
                       intelligence=mr.finalize(mr.parse_record(raw), LINES, lambda s: s, model='m', at=created),
                       messages=[{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'b'}], **extra)
        storage.save_session(session)
        return session

    def tasks(self, **query):
        return [a['task'] for a in self.client.get('/api/actions', params=query).json()['actions']]

    def test_open_actions_across_meetings_sorted_by_due_date_then_undated(self):
        self.assertEqual(self.tasks(), ['Sign contract', 'Send list', 'Call back', 'Book room'])  # dated first; undated newest meeting first
        body = self.client.get('/api/actions').json()
        self.assertEqual(body['owners'], ['Maya Okafor', 'Tom'])
        self.assertEqual({m['title'] for m in body['meetings']}, {'Budget review', 'Vendor call'})
        self.assertEqual(body['actions'][0]['due'], '2026-12-01')  # the date as stated

    def test_filters(self):
        self.assertEqual(self.tasks(owner='tom'), ['Sign contract', 'Send list'])
        self.assertEqual(self.tasks(meeting=self.first['id']), ['Send list', 'Book room'])
        self.assertEqual(self.tasks(status='done'), [])
        self.assertEqual(len(self.tasks(status='all')), 4)

    def test_status_update_and_separate_storage(self):
        url = f"/api/actions/{self.second['id']}/A1"
        before = storage.load_session(self.second['id'])['intelligence']
        reply = self.client.patch(url, json={'status': 'done'})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(self.tasks(), ['Send list', 'Call back', 'Book room'])
        self.assertEqual(self.tasks(status='done'), ['Sign contract'])
        saved = storage.load_session(self.second['id'])
        self.assertEqual(saved['intelligence'], before)  # the model's record is untouched
        self.assertEqual(saved['action_state']['A1']['status'], 'done')
        self.assertTrue(saved['action_state']['A1']['updated_at'])
        self.assertEqual(storage.public_session(saved)['action_state']['A1']['status'], 'done')
        self.client.patch(url, json={'status': 'open'})
        self.assertEqual(len(self.tasks()), 4)

    def test_bad_status_unknown_action_and_unknown_meeting(self):
        self.assertEqual(self.client.patch(f"/api/actions/{self.first['id']}/A1", json={'status': 'maybe'}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/actions/{self.first['id']}/A9", json={'status': 'done'}).status_code, 404)
        self.assertEqual(self.client.patch('/api/actions/web_missing/A1', json={'status': 'done'}).status_code, 404)

    def test_archived_meetings_are_hidden_unless_asked_for(self):
        storage.set_session_flags(self.first['id'], archived=True)
        self.assertEqual(self.tasks(), ['Sign contract', 'Call back'])
        self.assertEqual(len(self.tasks(include_archived='true')), 4)

    def test_old_prose_meetings_and_plain_chats_contribute_nothing(self):
        old = storage.new_session()
        old.update(session_type='meeting', transcript='t', intelligence='prose', mode='meeting_ready',
                   messages=[{'role': 'user', 'content': 't'}, {'role': 'assistant', 'content': 'prose'}])
        storage.save_session(old)
        self.assertEqual(len(self.tasks()), 4)

    def test_actions_page_is_served_and_linked_from_the_navigation(self):
        self.assertEqual(self.client.get('/actions').status_code, 200)
        for page in ('/', '/settings', '/adviser', '/memory', '/models'):
            self.assertIn('href="/actions"', self.client.get(page).text, page)


class TelegramTextTests(unittest.TestCase):
    def test_summary_has_brief_decisions_actions_and_the_unverified_count(self):
        record = finalize(record_json(brief='# Heading\n**Bold** first line\nSecond line', decisions=[{'text': 'Ship it', 'lines': [1]}, {'text': 'Ghost', 'lines': [88]}]))
        text = '\n'.join(mr.telegram_messages(record))
        self.assertTrue(text.startswith('Heading\nBold first line\nSecond line'))
        self.assertIn('Decisions\n• Ship it [L1]\n• Ghost [L88] (unverified)', text)
        self.assertIn('• Tom: Send the vendor list (due Friday) [L3]', text)
        self.assertIn('1 item unverified', text)
        self.assertIn('1 risks', text)

    def test_missing_owner_and_due_are_spelled_out(self):
        record = finalize(record_json(actions=[{'task': 'Do it', 'lines': [1]}]))
        self.assertIn('• No owner: Do it (due not stated)', '\n'.join(mr.telegram_messages(record)))

    def test_long_summaries_are_split_to_fit(self):
        many = [{'text': f'Decision number {n} ' + 'word ' * 40, 'lines': [1]} for n in range(60)]
        messages = mr.telegram_messages(finalize(record_json(decisions=many)))
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(sum(2 if ord(c) > 0xFFFF else 1 for c in m) <= 4000 for m in messages))
        self.assertIn('Decision number 59', '\n'.join(messages))

    def test_split_text_handles_emoji_and_very_long_lines(self):
        pieces = mr.split_text('😀' * 4100)
        self.assertEqual(''.join(pieces), '😀' * 4100)
        self.assertTrue(all(sum(2 if ord(c) > 0xFFFF else 1 for c in m) <= 4000 for m in pieces))

    def test_failed_structure_and_legacy_prose(self):
        failed = mr.prose_record('Plain brief', model='m', at='t', failure='bad JSON')
        text = '\n'.join(mr.telegram_messages(failed))
        self.assertIn('Plain brief', text)
        self.assertIn('Structured part failed: bad JSON', text)
        self.assertEqual(mr.telegram_messages('An old prose analysis.'), ['An old prose analysis.'])

    def test_actions_text(self):
        rows = [{'owner': 'Tom', 'task': 'Send', 'due': None, 'meeting_title': 'Budget'}]
        self.assertEqual(mr.telegram_actions(rows), ['Open actions (1)\n• Tom: Send (due not stated) — Budget'])
        self.assertEqual(mr.telegram_actions([]), ['No open actions.'])


class TelegramActionsCommandTests(unittest.TestCase):
    def test_actions_command_lists_open_actions_read_only(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'terms.txt'), ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)
        session = storage.new_session()
        session.update(title='Budget', session_type='meeting', transcript='x', mode='meeting_ready',
                       intelligence=finalize(), messages=[{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'b'}])
        storage.save_session(session)
        from telegram_transport import TelegramTransport
        transport = TelegramTransport(PrivacyEngine(FakeAnalyzer()), asyncio.Lock(), {1})
        sent = []
        message = SimpleNamespace(reply_text=AsyncMock(side_effect=lambda text, **kw: sent.append(text)))
        update = SimpleNamespace(effective_message=message)
        before = storage.load_session(session['id'])
        asyncio.run(transport.actions(update, SimpleNamespace(args=[])))
        self.assertIn('Tom: Send the vendor list (due Friday) — Budget', sent[0])
        self.assertEqual(storage.load_session(session['id']), before)  # nothing written


if __name__ == '__main__':
    unittest.main()
