import asyncio
import json
import random
import re
import tempfile
import unittest
from contextlib import aclosing
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

import engine
import storage
from privacy_engine import PrivacyEngine, StreamRehydrator


class FakeAnalyzer:
    def analyze(self, text, language):
        return [SimpleNamespace(start=m.start(), end=m.end(), score=.99, entity_type='EMAIL_ADDRESS')
                for m in re.finditer(r'[\w.]+@[\w.]+\.[a-z]+', text)]


def sse(*events):
    body = ': OPENROUTER PROCESSING\n\n'
    for event in events:
        body += ('data: [DONE]\n\n' if event == 'DONE' else f'data: {json.dumps(event)}\n\n')
    return body


def delta(text):
    return {'choices': [{'delta': {'content': text}}]}


class RehydratorTests(unittest.TestCase):
    def test_chunking_never_changes_the_result(self):
        mapping = {'PERSON__1': 'Ann Lee', 'CLIENT__12': 'Acme'}
        text = 'PERSON__1 met CLIENT__12, xABC__1 stays, PERSON__10 stays, (PERSON__1) and PERSON__1'
        expected = PrivacyEngine.rehydrate(text, mapping)
        rng = random.Random(7)
        for _ in range(300):
            cuts = sorted(rng.sample(range(1, len(text)), rng.randint(1, 12)))
            pieces = [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]
            rehydrator = StreamRehydrator(mapping)
            out = ''.join(rehydrator.feed(piece) for piece in pieces) + rehydrator.flush()
            self.assertEqual(out, expected)

    def test_token_split_across_chunks_is_restored(self):
        rehydrator = StreamRehydrator({'PERSON__1': 'Ann'})
        self.assertEqual(rehydrator.feed('Hi PERS'), 'Hi ')
        self.assertEqual(rehydrator.feed('ON__'), '')
        self.assertEqual(rehydrator.feed('1, hello'), 'Ann, ')
        self.assertEqual(rehydrator.flush(), 'hello')


class StreamCompleteTests(unittest.TestCase):
    def run_stream(self, body, status=200):
        original = httpx.AsyncClient
        handler = lambda request: httpx.Response(status, text=body, headers={'content-type': 'text/event-stream'})
        settled = []

        async def go():
            pieces, error = [], None
            try:
                async with aclosing(engine.stream_complete([{'role': 'user', 'content': 'hi'}], 'm')) as stream:
                    async for piece in stream:
                        pieces.append(piece)
            except engine.ChatError as exc:
                error = exc
            return pieces, error

        async def fake_reserve(*args):
            return 'key', 100, (Decimal('0.000001'), Decimal('0.000002'), Decimal(0)), 'reservation'

        with patch.object(engine, '_reserve_stream', fake_reserve), \
                patch.object(engine.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw)), \
                patch.object(engine.monthly_budget, 'settle', side_effect=lambda r, u: settled.append(u)):
            pieces, error = asyncio.run(go())
        return pieces, error, settled

    def test_deltas_and_usage_settlement(self):
        usage = {'prompt_tokens': 3, 'completion_tokens': 2, 'cost': 0.001}
        pieces, error, settled = self.run_stream(sse(delta('Hel'), delta('lo'),
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': usage}, 'DONE'))
        self.assertEqual((pieces, error), (['Hel', 'lo'], None))
        self.assertEqual(settled, [usage])

    def test_missing_usage_retains_reservation(self):
        pieces, error, settled = self.run_stream(sse(delta('Hello'), 'DONE'))
        self.assertEqual(pieces, ['Hello'])
        self.assertEqual(settled, [None])  # settle(None) is a no-op, so the full reservation stays

    def test_mid_stream_error_and_http_error_are_sanitized(self):
        pieces, error, _ = self.run_stream(sse(delta('Part'), {'error': {'message': 'secret provider text'}}))
        self.assertEqual(pieces, ['Part'])
        self.assertIsInstance(error, engine.ChatError)
        self.assertNotIn('secret', str(error))
        pieces, error, _ = self.run_stream('{"error":"nope"}', status=402)
        self.assertEqual(pieces, [])
        self.assertIsInstance(error, engine.ChatError)

    def test_empty_completion_is_an_error(self):
        _, error, _ = self.run_stream(sse({'choices': [{'delta': {}}]}, 'DONE'))
        self.assertIsInstance(error, engine.ChatError)


class StreamChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'sensitive_terms.txt'),
                            ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)
        change = patch.object(storage, 'settings', return_value={'model': 'm', 'sensitive_terms': 'CLIENT: Acme'})
        change.start()
        self.addCleanup(change.stop)
        self.privacy = PrivacyEngine(FakeAnalyzer())

    def fake_stream(self, pieces, fail=False):
        sent = []

        async def stream(messages, model, max_tokens=None):
            sent.append(messages)
            for piece in pieces:
                yield piece
            if fail:
                raise engine.ChatError('boom')
        return stream, sent

    def collect(self, session, text, stream, stop_after_first_delta=False):
        events = []

        async def go():
            async with aclosing(engine.stream_chat(session, text, self.privacy)) as gen:
                async for event in gen:
                    events.append(event)
                    if stop_after_first_delta and event['type'] == 'delta':
                        break
        with patch.object(engine, 'stream_complete', stream):
            asyncio.run(go())
        return events

    def test_stream_restores_tokens_and_saves_once(self):
        stream, sent = self.fake_stream(['Hi CLI', 'ENT__1, bye'])
        session = storage.new_session()
        events = self.collect(session, 'Tell Acme hello', stream)
        self.assertNotIn('Acme', json.dumps(sent))
        self.assertEqual([e['type'] for e in events][0], 'start')
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'delta'), 'Hi Acme, bye')
        self.assertEqual(events[-1]['type'], 'done')
        saved = storage.load_session(session['id'])
        self.assertEqual(saved['messages'][-1], {'role': 'assistant', 'content': 'Hi Acme, bye', 'model': 'm'})
        self.assertNotIn('token_map', events[-1]['session'])

    def test_abort_saves_partial_reply_marked_stopped(self):
        stream, _ = self.fake_stream(['First part. ', 'second part.'])
        session = storage.new_session()
        self.collect(session, 'Question', stream, stop_after_first_delta=True)
        saved = storage.load_session(session['id'])
        self.assertEqual(saved['messages'][0]['content'], 'Question')
        self.assertTrue(saved['messages'][1]['stopped'])
        self.assertIn('First part.', saved['messages'][1]['content'])

    def test_error_before_any_text_saves_nothing_and_error_after_text_keeps_partial(self):
        session = storage.new_session()
        stream, _ = self.fake_stream([], fail=True)
        events = self.collect(session, 'Question', stream)
        self.assertEqual(events[-1], {'type': 'error', 'message': 'boom', 'saved': False})
        self.assertFalse(storage.session_path(session['id']).exists())
        stream, _ = self.fake_stream(['Some text '], fail=True)
        events = self.collect(session, 'Question', stream)
        self.assertTrue(events[-1]['saved'])
        self.assertTrue(storage.load_session(session['id'])['messages'][-1]['stopped'])

    def test_local_titles_need_no_model_call_and_never_replace_existing_titles(self):
        stream, _ = self.fake_stream(['Sure'])
        with patch.object(engine, 'complete', side_effect=AssertionError('Titles must not call the model')):
            session = storage.new_session()
            events = self.collect(session, "summarize what's in the document", stream)
        self.assertEqual(events[-1]['session']['title'], "Summarize what's in the document")
        self.assertTrue(storage.load_session(session['id'])['title_generated'])
        # Later messages never retitle it.
        self.collect(session, 'A completely different second question', stream)
        self.assertEqual(storage.load_session(session['id'])['title'], "Summarize what's in the document")
        # Manual renames and existing titles survive the first reply.
        renamed = storage.new_session(); renamed['title'] = 'My title'
        self.collect(renamed, 'Hello', stream)
        self.assertEqual(storage.load_session(renamed['id'])['title'], 'My title')

    def test_title_forms(self):
        long = 'word ' * 30
        self.assertEqual(engine.local_title({'content': long})[-1], '…')
        self.assertLessEqual(len(engine.local_title({'content': long})), 61)
        self.assertEqual(engine.local_title({'content': '  hello\n world  '}), 'Hello world')
        attachments = [{'name': 'a.pdf'}, {'name': 'b.png'}]
        self.assertEqual(engine.local_title({'content': 'x', 'typed': '', 'attachments': attachments}), 'Attachment: a.pdf +1')
        self.assertEqual(engine.local_title({'content': 'Summarise\n\n[Attached file: a.pdf]\nbody', 'typed': 'Summarise',
                                             'attachments': attachments[:1]}), 'Summarise · a.pdf')
        self.assertEqual(engine.local_title({'content': '會議紀要' * 30})[-1], '…')
        self.assertIsNone(engine.local_title({'content': '   '}))

    def test_stopped_first_reply_still_gets_a_title(self):
        stream, _ = self.fake_stream(['First part. ', 'second part.'])
        session = storage.new_session()
        self.collect(session, 'Question about budgets', stream, stop_after_first_delta=True)
        self.assertEqual(storage.load_session(session['id'])['title'], 'Question about budgets')

    def test_meeting_question_streams_with_full_transcript(self):
        session = storage.new_session()
        session.update(session_type='meeting', transcript='Acme transcript', intelligence='Summary',
                       mode='meeting_ready', messages=[{'role': 'user', 'content': 'Acme transcript'},
                                                       {'role': 'assistant', 'content': 'Summary'}])
        stream, sent = self.fake_stream(['Answer'])
        events = self.collect(session, 'What about Acme?', stream)
        self.assertNotIn('Acme', json.dumps(sent))
        self.assertRegex(json.dumps(sent), r'CLIENT__\d+ transcript')
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(storage.load_session(session['id'])['messages'][-1]['content'], 'Answer')


class Tier1FeatureTests(unittest.TestCase):
    """Custom instructions per channel, rename, search and settings round trip."""
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
        self.privacy = PrivacyEngine(FakeAnalyzer())
        import app
        change = patch.object(app, 'PrivacyEngine', return_value=self.privacy)
        change.start()
        self.addCleanup(change.stop)
        app.chat_lock = asyncio.Lock()
        from fastapi.testclient import TestClient
        self.client = TestClient(app.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def saved_session(self, title, messages, **extra):
        session = storage.new_session()
        session.update(title=title, messages=messages, **extra)
        storage.save_session(session)
        return session

    def test_instructions_are_separate_per_channel_and_privacy_processed(self):
        response = self.client.put('/api/settings', json={'model': 'test/model', 'sensitive_terms': 'CLIENT: Acme',
            'web_instructions': 'Web: be brief about Acme.', 'telegram_instructions': 'Telegram: use short lines.'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['web_instructions'], 'Web: be brief about Acme.')
        seen = []

        async def completion(messages, model, *args, **kwargs):
            seen.append(messages[0]['content'])
            return 'ok'
        with patch.object(engine, 'complete', completion):
            asyncio.run(engine.chat(storage.new_session(), 'Hello', self.privacy))
            asyncio.run(engine.chat(storage.new_session('telegram'), 'Hello', self.privacy, channel='telegram'))
        self.assertIn('Web: be brief', seen[0]); self.assertNotIn('Telegram:', seen[0]); self.assertNotIn('Acme', seen[0])
        self.assertIn('Telegram: use short lines.', seen[1]); self.assertNotIn('Web:', seen[1])

    def test_empty_instructions_leave_prompts_unchanged_and_omitted_setting_keeps_saved(self):
        seen = []

        async def completion(messages, model, *args, **kwargs):
            seen.append(messages[0]['content'])
            return 'ok'
        with patch.object(engine, 'complete', completion):
            asyncio.run(engine.chat(storage.new_session(), 'Hello', self.privacy))
        self.assertNotIn('standing instructions', seen[0])
        self.client.put('/api/settings', json={'model': 'test/model', 'sensitive_terms': '', 'web_instructions': 'Keep me'})
        again = self.client.put('/api/settings', json={'model': 'test/model', 'sensitive_terms': ''})
        self.assertEqual(again.json()['web_instructions'], 'Keep me')
        too_long = self.client.put('/api/settings', json={'model': 'test/model', 'sensitive_terms': '', 'web_instructions': 'x' * 2001})
        self.assertEqual(too_long.status_code, 422)

    def test_meeting_questions_use_the_calling_channels_instructions(self):
        self.client.put('/api/settings', json={'model': 'test/model', 'sensitive_terms': '',
            'web_instructions': 'WEBONLY', 'telegram_instructions': 'TGONLY'})
        session = storage.new_session()
        session.update(session_type='meeting', transcript='T', intelligence='I', mode='meeting_ready',
                       messages=[{'role': 'user', 'content': 'T'}, {'role': 'assistant', 'content': 'I'}])
        seen = []

        async def completion(messages, model, *args, **kwargs):
            seen.append(messages[0]['content'])
            return 'ok'
        with patch.object(engine, 'complete', completion):
            asyncio.run(engine.ask_meeting(session, 'Q1', self.privacy, channel='telegram'))
            asyncio.run(engine.ask_meeting(session, 'Q2', self.privacy))
        self.assertIn('TGONLY', seen[0]); self.assertNotIn('WEBONLY', seen[0])
        self.assertIn('WEBONLY', seen[1]); self.assertNotIn('TGONLY', seen[1])

    def test_rename_is_sticky_keeps_order_and_search_covers_bodies_and_transcripts(self):
        older = self.saved_session('Older', [{'role': 'user', 'content': 'Budget forecast notes'}])
        newer = self.saved_session('Newer', [{'role': 'user', 'content': 'Lunch plans'}])
        meeting = self.saved_session('Weekly sync', [{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'y'}],
                                     session_type='meeting', transcript='Discussed the Zanzibar rollout', intelligence='y', mode='meeting_ready')
        self.assertEqual([r['id'] for r in self.client.get('/api/sessions', params={'q': 'FORECAST'}).json()], [older['id']])
        self.assertEqual([r['id'] for r in self.client.get('/api/sessions', params={'q': 'zanzibar'}).json()], [meeting['id']])
        self.assertEqual(self.client.get('/api/sessions', params={'q': 'nothing here'}).json(), [])
        before = storage.load_session(older['id'])['updated_at']
        renamed = self.client.patch('/api/sessions/' + older['id'], json={'title': '  Q3   plan  '})
        self.assertEqual(renamed.json()['title'], 'Q3 plan')
        stored = storage.load_session(older['id'])
        self.assertTrue(stored['title_generated'])
        self.assertEqual(stored['updated_at'], before)
        self.assertEqual(self.client.patch('/api/sessions/' + older['id'], json={'title': '   '}).status_code, 422)
        self.assertEqual(self.client.patch('/api/sessions/missing', json={'title': 'x'}).status_code, 404)
        self.assertEqual(len(self.client.get('/api/sessions').json()), 3)


class MeetingModelTests(unittest.TestCase):
    """Meeting analysis and meeting questions use their own model on every channel; all else uses the main model."""
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
        self.privacy = PrivacyEngine(FakeAnalyzer())
        import app
        change = patch.object(app, 'PrivacyEngine', return_value=self.privacy)
        change.start()
        self.addCleanup(change.stop)
        app.chat_lock = asyncio.Lock()
        from fastapi.testclient import TestClient
        self.client = TestClient(app.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.models = []

        async def completion(messages, model, *args, **kwargs):
            self.models.append(model)
            return 'ok'

        async def stream(messages, model, max_tokens=None):
            self.models.append(model)
            yield 'ok'
        for target, replacement in (('complete', completion), ('stream_complete', stream)):
            change = patch.object(engine, target, replacement)
            change.start()
            self.addCleanup(change.stop)

    def meeting(self):
        session = storage.new_session()
        session.update(session_type='meeting', transcript='T', intelligence='I', mode='meeting_ready',
                       messages=[{'role': 'user', 'content': 'T'}, {'role': 'assistant', 'content': 'I'}])
        return session

    def stream(self, session, text):
        async def go():
            async with aclosing(engine.stream_chat(session, text, self.privacy)) as events:
                return [event async for event in events]
        return asyncio.run(go())

    def test_each_kind_of_chat_uses_its_own_model_on_web_and_telegram(self):
        self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': '', 'meeting_model': 'meet/model'})
        self.assertEqual(self.client.get('/api/settings').json()['meeting_model'], 'meet/model')
        asyncio.run(engine.chat(storage.new_session(), 'Hello', self.privacy))                          # web chat
        asyncio.run(engine.chat(storage.new_session('telegram'), 'Hello', self.privacy, channel='telegram'))  # Telegram chat
        self.stream(storage.new_session(), 'Hello')                                                     # streamed web chat
        self.assertEqual(self.models, ['chat/model'] * 3)
        self.models.clear()
        asyncio.run(engine.ask_meeting(self.meeting(), 'Q', self.privacy))                              # web meeting Q&A
        asyncio.run(engine.ask_meeting(self.meeting(), 'Q', self.privacy, channel='telegram'))          # Telegram /ask
        self.stream(self.meeting(), 'Q')                                                                # streamed meeting Q&A
        asyncio.run(engine.chat(self.meeting(), 'Q', self.privacy))                                     # chat() on a meeting session
        self.assertEqual(self.models, ['meet/model'] * 4)
        self.models.clear()
        asyncio.run(engine.analyze_meeting(storage.new_session('telegram'), 'raw transcript', self.privacy))
        self.assertEqual(self.models, ['meet/model', 'meet/model'])  # analysis and its title call

    def test_blank_meeting_model_follows_the_main_model_and_can_be_cleared(self):
        self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': ''})
        asyncio.run(engine.ask_meeting(self.meeting(), 'Q', self.privacy))
        self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': '', 'meeting_model': 'meet/model'})
        self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': ''})  # omitted: unchanged
        asyncio.run(engine.ask_meeting(self.meeting(), 'Q', self.privacy))
        self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': '', 'meeting_model': ''})
        asyncio.run(engine.ask_meeting(self.meeting(), 'Q', self.privacy))
        self.assertEqual(self.models, ['chat/model', 'meet/model', 'chat/model'])

    def test_model_endpoint_purposes_and_validation(self):
        self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': ''})
        saved = self.client.put('/api/settings/model', json={'model': 'meet/model', 'purpose': 'meeting'}).json()
        self.assertEqual(saved, {'model': 'chat/model', 'meeting_model': 'meet/model'})
        self.assertEqual(self.client.put('/api/settings/model', json={'model': 'other/chat'}).json()['meeting_model'], 'meet/model')
        self.assertEqual(self.client.put('/api/settings/model', json={'model': '', 'purpose': 'meeting'}).json()['meeting_model'], '')
        self.assertEqual(self.client.put('/api/settings/model', json={'model': ''}).status_code, 422)  # main model cannot be blank
        for bad in ({'model': 'nomodel', 'purpose': 'meeting'}, {'model': 'a/b', 'purpose': 'other'}):
            self.assertEqual(self.client.put('/api/settings/model', json=bad).status_code, 422)
        self.assertEqual(self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': '', 'meeting_model': 'bad'}).status_code, 422)
        self.assertEqual(self.client.get('/api/settings').json()['model'], 'other/chat')


class ModelsPageTests(Tier1FeatureTests):
    """The Models page route and the model-only setting endpoint."""
    def test_models_page_and_model_endpoint_change_only_the_model(self):
        self.assertEqual(self.client.get('/models').status_code, 200)
        self.assertIn('Models and prices', self.client.get('/models').text)
        self.client.put('/api/settings', json={'model': 'old/model', 'sensitive_terms': 'CLIENT: Acme', 'chat_diagnosis': True,
                                               'web_instructions': 'Keep me', 'review_extracted_text': True})
        self.client.put('/api/settings/favourite', json={'model': 'x/fav', 'favourite': True})
        changed = self.client.put('/api/settings/model', json={'model': 'new/model'})
        self.assertEqual(changed.json(), {'model': 'new/model', 'meeting_model': ''})
        settings = self.client.get('/api/settings').json()
        self.assertEqual((settings['model'], settings['chat_diagnosis'], settings['web_instructions'], settings['review_extracted_text']),
                         ('new/model', True, 'Keep me', True))
        self.assertEqual(settings['sensitive_terms'], 'CLIENT: Acme')
        self.assertEqual(settings['favourites'], ['x/fav'])
        for bad in ('nomodel', '', 'a b/c'):
            self.assertEqual(self.client.put('/api/settings/model', json={'model': bad}).status_code, 422)
        self.assertEqual(self.client.get('/api/settings').json()['model'], 'new/model')


if __name__ == '__main__':
    unittest.main()
