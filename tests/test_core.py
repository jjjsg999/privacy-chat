import asyncio
import json
import re
import tempfile
import unittest
import httpx
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import app
import engine
import storage
from privacy_engine import PrivacyEngine, parse_terms


class FakeAnalyzer:
    def analyze(self, text, language):
        return [SimpleNamespace(start=m.start(), end=m.end(), score=.99, entity_type='EMAIL_ADDRESS')
                for m in re.finditer(r'[\w.]+@[\w.]+\.[a-z]+', text)]


class PrivacyTests(unittest.TestCase):
    def test_terms_pii_roundtrip_and_isolation(self):
        privacy = PrivacyEngine(FakeAnalyzer())
        text = 'Acme and ACME: jane@example.com. Literal PERSON__1.'
        mapping = {}
        masked = privacy.tokenize(text, mapping, parse_terms('CLIENT: Acme\n# ignore'))
        self.assertNotIn('Acme', masked)
        self.assertNotIn('ACME', masked)
        self.assertNotIn('jane@example.com', masked)
        self.assertIn('CLIENT__', masked)
        self.assertEqual(privacy.rehydrate(masked, mapping), text)
        second = {}
        privacy.tokenize('other@example.com', second, [])
        self.assertNotIn('jane@example.com', second.values())
        self.assertEqual(privacy.tokenize(text, mapping, parse_terms('CLIENT: Acme')), masked)

    def test_overlapping_pii_masks_whole_union(self):
        analyzer = FakeAnalyzer()
        analyzer.analyze = lambda **kwargs: [
            SimpleNamespace(start=0, end=5, score=.9, entity_type='PERSON'),
            SimpleNamespace(start=3, end=8, score=.8, entity_type='LOCATION')]
        mapping = {}
        privacy = PrivacyEngine(analyzer)
        masked = privacy.tokenize('abcdefgh', mapping, [])
        self.assertEqual(masked, 'PERSON__1')
        self.assertEqual(privacy.rehydrate(masked, mapping), 'abcdefgh')

    def test_custom_term_does_not_corrupt_placeholder(self):
        privacy = PrivacyEngine(FakeAnalyzer())
        text = 'PERSON__1 and 1'
        mapping = {}
        masked = privacy.tokenize(text, mapping, parse_terms('1'))
        self.assertEqual(privacy.rehydrate(masked, mapping), text)
        text = 'İ ı I i'
        masked = privacy.tokenize(text, mapping, parse_terms('PERSON: i'))
        self.assertEqual(privacy.rehydrate(masked, mapping), text)
        self.assertNotIn('İ', masked)
        text = 'Ann attends the annual review.'
        masked = privacy.tokenize(text, mapping, parse_terms('PERSON: Ann'))
        self.assertIn('annual', masked)
        self.assertEqual(privacy.rehydrate(masked, mapping), text)


class CoreSmokeTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'sensitive_terms.txt'),
                            ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)
        privacy = PrivacyEngine(FakeAnalyzer())
        change = patch.object(app, 'PrivacyEngine', return_value=privacy)
        change.start()
        self.addCleanup(change.stop)
        app.chat_lock = asyncio.Lock()
        self.client = TestClient(app.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_delete_session_removes_only_target_and_telegram_mapping(self):
        first = storage.new_session('telegram')
        first['messages'] = [{'role': 'user', 'content': 'Saved meeting'}]
        first['token_map'] = {'PERSON__1': 'Private name'}
        second = storage.new_session()
        second['messages'] = [{'role': 'user', 'content': 'Keep this'}]
        storage.save_session(first)
        storage.save_session(second)
        storage.write_json(storage.DATA / 'telegram_active.json', {'123:123': first['id'], '456:456': second['id']})
        response = self.client.delete('/api/sessions/' + first['id'])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(storage.session_path(first['id']).exists())
        self.assertEqual(self.client.get('/api/sessions/' + first['id']).status_code, 404)
        self.assertEqual(self.client.delete('/api/sessions/' + first['id']).status_code, 404)
        self.assertEqual(storage.load_session(second['id'])['messages'], second['messages'])
        self.assertEqual(json.loads((storage.DATA / 'telegram_active.json').read_text()), {'456:456': second['id']})
        self.assertEqual(self.client.delete('/api/sessions/invalid.id').status_code, 404)

    def test_chat_persistence_titles_and_updated_global_terms(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/settings').status_code, 200)
        self.assertEqual(self.client.get('/api/sessions').json(), [])
        self.assertEqual(self.client.put('/api/settings', json={'model':'test/model','sensitive_terms':'CLIENT: Acme'}).status_code, 200)
        calls = []

        async def completion(messages, model):
            calls.append(messages)
            self.assertEqual(model, 'test/model')
            outbound = json.dumps(messages)
            self.assertNotIn('Acme', outbound)
            self.assertNotIn('jane@example.com', outbound)
            if messages[-1]['content'].startswith('Give this conversation'):
                return 'CLIENT__1 planning'
            return 'Discuss CLIENT__1 with EMAIL_ADDRESS__2.'

        with patch.object(engine, 'complete', side_effect=completion):
            response = self.client.post('/api/chat', json={'message':'Acme jane@example.com ProjectSecret'})
            self.assertEqual(response.status_code, 200, response.text)
            session = response.json()
            session_id = session['id']
            self.assertNotIn('token_map', session)
            self.assertIn('Acme', session['messages'][-1]['content'])
            self.client.put('/api/settings', json={'model':'test/model','sensitive_terms':'CLIENT: Acme\nSYSTEM: ProjectSecret'})
            for _ in range(3):
                response = self.client.post('/api/chat', json={'session_id':session_id,'message':'Please continue.'})
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(calls), 5)  # Four replies, one stable title.
            self.assertNotIn('ProjectSecret', json.dumps(calls[1:]))
            self.assertEqual(response.json()['title'], 'Acme planning')
        persisted = storage.load_session(session_id)
        self.assertEqual(len(persisted['messages']), 8)
        self.assertTrue(persisted['token_map'])
        self.assertEqual(self.client.get(f'/api/sessions/{session_id}').json()['id'], session_id)
        self.assertEqual(len(self.client.get('/api/sessions').json()), 1)

    def test_failure_and_blank_input_do_not_save(self):
        self.assertEqual(self.client.post('/api/chat', json={'message':'   '}).status_code, 422)
        with patch.object(engine, 'complete', side_effect=engine.ChatError('Unavailable')):
            self.assertEqual(self.client.post('/api/chat', json={'message':'Hello'}).status_code, 502)
        self.assertEqual(storage.list_sessions(), [])
        self.assertEqual(self.client.get('/api/sessions/missing').status_code, 404)
        self.assertEqual(self.client.put('/api/settings', json={'model':'','sensitive_terms':''}).status_code, 422)
        self.assertEqual(self.client.post('/api/chat', headers={'Origin':'https://outside.example'}, json={'message':'Hello'}).status_code, 403)

    def test_title_failure_keeps_reply(self):
        with patch.object(engine, 'complete', new=AsyncMock(side_effect=['One', 'Two', 'Three', engine.ChatError('No title')])):
            session_id = None
            for _ in range(3):
                response = self.client.post('/api/chat', json={'session_id':session_id, 'message':'Hello'})
                self.assertEqual(response.status_code, 200)
                session_id = response.json()['id']
            self.assertEqual(len(storage.load_session(session_id)['messages']), 6)
            self.assertFalse(response.json()['title_generated'])
        with patch.object(engine, 'complete', new=AsyncMock(side_effect=['Fourth', '""'])):
            response = self.client.post('/api/chat', json={'session_id':session_id, 'message':'Continue'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(storage.load_session(session_id)['messages']), 8)
            self.assertFalse(response.json()['title_generated'])

    def test_openrouter_settings_catalogue_selection_and_safe_test(self):
        response = self.client.put('/api/settings/favourite', json={'model':'test/favourite', 'favourite':True})
        self.assertEqual(response.json()['favourites'], ['test/favourite'])
        secret = 'test-secret-never-return-this'
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': secret}):
            settings = self.client.get('/api/settings')
            self.assertTrue(settings.json()['openrouter']['configured'])
            self.assertNotIn(secret, settings.text)
            provider = httpx.Response(200, request=httpx.Request('GET', 'https://openrouter.ai/api/v1/models'),
                json={'data':[{'id':'test/new-model','name':'New model','context_length':32000,'pricing':{'prompt':'0.000001'}}]})
            with patch.object(engine.httpx.AsyncClient, 'get', new=AsyncMock(return_value=provider)):
                response = self.client.get('/api/openrouter/models')
            self.assertEqual(response.json()[0]['id'], 'test/new-model')
            self.assertNotIn(secret, response.text)
            self.client.put('/api/settings', json={'model':'test/new-model','sensitive_terms':'CLIENT: Acme'})
            self.assertEqual(storage.settings()['model'], 'test/new-model')
            self.assertEqual(self.client.get('/api/settings').json()['favourites'], ['test/favourite'])
            self.client.put('/api/settings/favourite', json={'model':'test/favourite', 'favourite':False})
            self.assertEqual(storage.settings()['favourites'], [])
            self.assertEqual(storage.settings()['model'], 'test/new-model')
            self.assertEqual(storage.settings()['sensitive_terms'], 'CLIENT: Acme')
            with patch.object(engine, 'complete', new=AsyncMock(return_value='OK')) as completion:
                response = self.client.post('/api/openrouter/test')
                self.assertTrue(response.json()['ok'])
                self.assertEqual(completion.call_args.args[1], 'test/new-model')
                self.assertEqual(completion.call_args.kwargs['max_tokens'], 64)
            with patch.object(engine, 'complete', side_effect=engine.ChatError(secret)):
                response = self.client.post('/api/openrouter/test')
                self.assertFalse(response.json()['ok'])
                self.assertNotIn(secret, response.text)
            with patch.object(engine.httpx.AsyncClient, 'get', side_effect=httpx.ConnectError(secret)):
                response = self.client.get('/api/openrouter/models')
                self.assertEqual(response.status_code, 502)
                self.assertNotIn(secret, response.text)

    def test_cross_channel_visibility_and_meeting_continuation(self):
        blank = storage.new_session('telegram')
        storage.save_session(blank)
        pending = storage.new_session('telegram')
        pending.update(session_type='meeting', mode='meeting_capture', transcript='Draft',
                       messages=[{'role':'user','content':'Draft'}])
        storage.save_session(pending)
        self.assertEqual(self.client.get('/api/sessions').json(), [])
        self.assertEqual(self.client.get('/api/sessions/' + pending['id']).status_code, 404)
        meeting = storage.new_session('telegram')
        meeting.update(session_type='meeting',mode='meeting_ready', transcript='Full transcript: seven approvals.',
                       intelligence='Leadership analysis.', messages=[{'role':'user','content':'Full transcript: seven approvals.'},
                       {'role':'assistant','content':'Leadership analysis.'}])
        storage.save_session(meeting)
        self.assertEqual(self.client.get('/api/sessions').json()[0]['channel_origin'], 'telegram')
        with patch.object(engine, 'complete', new=AsyncMock(return_value='Seven approvals.')) as completion:
            response = self.client.post('/api/chat', json={'session_id':meeting['id'],'message':'How many approvals?'})
            self.assertEqual(response.json()['id'], meeting['id'])
            self.assertIn('seven approvals', json.dumps(completion.call_args.args[0]))
        self.assertEqual(len(storage.load_session(meeting['id'])['messages']), 4)


if __name__ == '__main__':
    unittest.main()
