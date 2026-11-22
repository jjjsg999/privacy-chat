"""Edit-and-resend, branching, pin/archive and per-model spend. Provider calls are mocked; nothing is paid."""
import asyncio
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import engine
import monthly_budget
import storage
from privacy_engine import PrivacyEngine
from test_streaming import FakeAnalyzer


class ChatActionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'sensitive_terms.txt'), ('CONFIG', root / 'config.json')]:
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
        self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': ''})
        self.sent = []
        self.fail = False

        async def stream(messages, model, max_tokens=None):
            self.sent.append(messages)
            if self.fail:
                raise engine.ChatError('boom')
            yield 'New reply'
        change = patch.object(engine, 'stream_complete', stream)
        change.start()
        self.addCleanup(change.stop)

    def chat(self, pairs=2, **extra):
        session = storage.new_session()
        messages = []
        for number in range(pairs):
            messages += [{'role': 'user', 'content': f'question {number}'},
                         {'role': 'assistant', 'content': f'answer {number}', 'model': 'chat/model'}]
        session.update(title='Planning', title_generated=True, messages=messages, token_map={'PERSON__1': 'Ann'}, **extra)
        storage.save_session(session)
        return session

    def stream(self, **body):
        response = self.client.post('/api/chat/stream', json=body)
        self.assertEqual(response.status_code, 200)
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    # --- edit and resend ---
    def test_edit_replaces_that_message_and_everything_after_it(self):
        session = self.chat(pairs=3)
        events = self.stream(session_id=session['id'], message='edited question', edit_index=2)
        self.assertEqual(events[-1]['type'], 'done')
        saved = storage.load_session(session['id'])
        self.assertEqual([m['content'] for m in saved['messages']],
                         ['question 0', 'answer 0', 'edited question', 'New reply'])
        # The model saw only the kept history plus the edit, never the discarded turns.
        sent = json.dumps(self.sent[-1])
        self.assertIn('edited question', sent)
        self.assertNotIn('question 1', sent)
        self.assertNotIn('answer 2', sent)

    def test_failed_edit_leaves_the_saved_conversation_untouched(self):
        session = self.chat(pairs=2)
        before = storage.load_session(session['id'])
        self.fail = True
        events = self.stream(session_id=session['id'], message='edited', edit_index=0)
        self.assertEqual(events[-1], {'type': 'error', 'message': 'boom', 'saved': False})
        self.assertEqual(storage.load_session(session['id'])['messages'], before['messages'])

    def test_edit_keeps_attached_file_text_and_rejects_bad_targets(self):
        session = self.chat(pairs=1)
        stored = storage.load_session(session['id'])
        stored['messages'][0] = {'role': 'user', 'content': 'summarise\n\n[Attached file: a.txt]\nBODY\n[End of attached file]',
                                 'typed': 'summarise', 'attachments': [{'name': 'a.txt', 'kind': 'exact', 'chars': 4}]}
        storage.save_session(stored)
        self.stream(session_id=session['id'], message='shorter please', edit_index=0)
        first = storage.load_session(session['id'])['messages'][0]
        self.assertEqual(first['typed'], 'shorter please')
        self.assertIn('[Attached file: a.txt]\nBODY', first['content'])
        self.assertTrue(first['content'].startswith('shorter please'))
        self.assertEqual(len(first['attachments']), 1)
        # Not a user message, out of range, no conversation, new files, and meetings are all refused.
        for body in ({'session_id': session['id'], 'message': 'x', 'edit_index': 1},
                     {'session_id': session['id'], 'message': 'x', 'edit_index': 9}):
            self.assertEqual(self.stream(**body)[-1]['type'], 'error')
        self.assertEqual(self.client.post('/api/chat/stream', json={'message': 'x', 'edit_index': 0}).status_code, 422)
        meeting = self.chat(pairs=1, session_type='meeting', transcript='T', intelligence='I', mode='meeting_ready')
        self.assertEqual(self.stream(session_id=meeting['id'], message='x', edit_index=0)[-1]['type'], 'error')

    # --- branching ---
    def test_branch_copies_history_to_an_independent_web_conversation(self):
        session = self.chat(pairs=2, channel_origin='telegram')
        response = self.client.post(f"/api/sessions/{session['id']}/branch", json={'message_index': 1})
        self.assertEqual(response.status_code, 200)
        branch = response.json()
        self.assertNotIn('token_map', branch)
        self.assertEqual(branch['title'], 'Planning (branch)')
        self.assertEqual(branch['channel_origin'], 'web')
        self.assertEqual([m['content'] for m in branch['messages']], ['question 0', 'answer 0'])
        stored = storage.load_session(branch['id'])
        self.assertEqual(stored['branched_from'], session['id'])
        self.assertEqual(stored['token_map'], {'PERSON__1': 'Ann'})
        stored['token_map']['PERSON__2'] = 'Bo'  # independent copy
        self.assertNotIn('PERSON__2', storage.load_session(session['id'])['token_map'])
        self.assertEqual(len(storage.load_session(session['id'])['messages']), 4)

    def test_branch_rejects_user_messages_bad_indexes_and_meetings(self):
        session = self.chat(pairs=1)
        for index in (0, 5):
            self.assertEqual(self.client.post(f"/api/sessions/{session['id']}/branch", json={'message_index': index}).status_code, 422)
        meeting = self.chat(pairs=1, session_type='meeting', transcript='T', intelligence='I', mode='meeting_ready')
        self.assertEqual(self.client.post(f"/api/sessions/{meeting['id']}/branch", json={'message_index': 1}).status_code, 422)
        self.assertEqual(self.client.post('/api/sessions/missing/branch', json={'message_index': 1}).status_code, 404)

    # --- pin and archive ---
    def test_pin_and_archive_keep_order_and_are_independent_of_rename(self):
        session = self.chat()
        stamp = storage.load_session(session['id'])['updated_at']
        url = f"/api/sessions/{session['id']}"
        self.assertEqual(self.client.patch(url, json={}).status_code, 422)
        self.assertTrue(self.client.patch(url, json={'pinned': True}).json()['pinned'])
        row = self.client.get('/api/sessions').json()[0]
        self.assertEqual((row['pinned'], row['archived']), (True, False))
        archived = self.client.patch(url, json={'archived': True}).json()
        self.assertEqual((archived['archived'], archived['pinned']), (True, False))  # archiving unpins
        self.assertEqual(self.client.patch(url, json={'title': 'Renamed'}).json()['title'], 'Renamed')
        self.assertTrue(self.client.get(url).json()['archived'])  # rename did not clear it
        self.assertEqual(storage.load_session(session['id'])['updated_at'], stamp)
        self.assertFalse(self.client.patch(url, json={'archived': False}).json()['archived'])
        self.assertEqual(self.client.patch('/api/sessions/missing', json={'pinned': True}).status_code, 404)


class ArtifactTests(ChatActionTests):
    """The sandboxed artifact frame and the prompt that asks for artifacts. (Inherits the app setup; its own tests re-run.)"""

    def test_frame_has_its_own_strict_policy_and_the_rest_of_the_app_keeps_the_normal_one(self):
        frame = self.client.get('/artifact-frame')
        self.assertEqual(frame.status_code, 200)
        policy = frame.headers['content-security-policy']
        for required in ("default-src 'none'", "connect-src 'none'", "form-action 'none'", "frame-src 'none'",
                         "base-uri 'none'", "frame-ancestors 'self'", 'sandbox allow-scripts'):
            self.assertIn(required, policy)
        self.assertNotIn('allow-same-origin', policy)
        self.assertIn("postMessage({type:'artifact-ready'}", frame.text)
        self.assertNotIn('/api/', frame.text)
        normal = self.client.get('/').headers['content-security-policy']
        self.assertEqual(normal, "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'self'")

    def test_web_chat_is_told_about_artifacts_but_meetings_and_telegram_chat_are_not(self):
        self.stream(message='make me a page')
        self.assertIn('<artifact type="TYPE"', self.sent[-1][0]['content'])
        prompt = self.sent[-1][0]['content']
        self.assertIn('default format is HTML', prompt)           # HTML is the default type
        self.assertIn('not Mermaid', prompt)                       # diagrams default to HTML too
        for office in ('Word', 'Excel', 'PowerPoint'):             # not built yet: ask for the stand-in
            self.assertIn(office, prompt)
        self.assertIn('Do not make one for a quick answer', prompt)  # ordinary chat stays chat
        meeting = self.chat(pairs=1, session_type='meeting', transcript='T', intelligence='I', mode='meeting_ready')
        self.sent.clear()
        self.stream(session_id=meeting['id'], message='question')
        self.assertNotIn('<artifact', json.dumps(self.sent))

        async def completion(messages, model, *args, **kwargs):
            self.sent.append(messages)
            return 'ok'
        self.sent.clear()
        with patch.object(engine, 'complete', completion):
            asyncio.run(engine.chat(storage.new_session('telegram'), 'Hello', PrivacyEngine(FakeAnalyzer()), channel='telegram'))
        self.assertNotIn('<artifact', json.dumps(self.sent))

    def test_artifact_content_is_restored_like_any_other_reply_text(self):
        async def stream(messages, model, max_tokens=None):
            yield '<artifact type="markdown" title="Note" id="n">\nMet PERS'
            yield 'ON__1 today.\n</artifact>'
        with patch.object(engine, 'stream_complete', stream):
            session = self.chat(pairs=1)
            events = self.stream(session_id=session['id'], message='write a note about Ann as an artifact')
        saved = storage.load_session(session['id'])['messages'][-1]['content']
        self.assertIn('Met Ann today.', saved)
        self.assertEqual(events[-1]['type'], 'done')


class SpendByModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        change = patch.object(storage, 'DATA', Path(self.temp.name) / 'data')
        change.start()
        self.addCleanup(change.stop)
        self.messages = [{'role': 'user', 'content': 'hi'}]
        self.rates = (Decimal('0.000001'), Decimal('0.000002'), Decimal(0))

    def test_spend_is_grouped_by_model_and_untracked_entries_are_separate(self):
        self.assertEqual(monthly_budget.spend_by_model()['models'], [])
        first = monthly_budget.reserve(self.messages, 10, self.rates, 'a/cheap')
        monthly_budget.settle(first, {'cost': 0.001})
        monthly_budget.reserve(self.messages, 10, self.rates, 'a/cheap')           # still unsettled
        monthly_budget.reserve(self.messages, 10, self.rates, 'b/pricey')
        monthly_budget.reserve(self.messages, 10, self.rates)                        # an entry with no model recorded
        result = monthly_budget.spend_by_model()
        by_model = {row['model']: row for row in result['models']}
        self.assertEqual(set(by_model), {'a/cheap', 'b/pricey'})
        self.assertEqual((by_model['a/cheap']['calls'], by_model['a/cheap']['unsettled']), (2, 1))
        self.assertGreater(Decimal(result['untracked_usd']), 0)
        self.assertEqual(Decimal(result['total_usd']),
                         sum((Decimal(r['usd']) for r in result['models']), Decimal(0)) + Decimal(result['untracked_usd']))
        self.assertTrue(result['available'])

    def test_unreadable_ledger_reports_unavailable_without_raising(self):
        monthly_budget.reserve(self.messages, 10, self.rates, 'a/b')
        (storage.DATA / 'monthly_budget' / 'ledger.json').write_text('{broken', encoding='utf-8')
        result = monthly_budget.spend_by_model()
        self.assertEqual((result['available'], result['models']), (False, []))


if __name__ == '__main__':
    unittest.main()
