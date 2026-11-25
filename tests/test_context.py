"""Context builder, masked-text cache, pins and context notes. Everything is invented and mocked; nothing is paid."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import context_builder as cb
import engine
import storage
from context_builder import ContextRefused, build_context, estimate_tokens
from privacy_engine import PrivacyEngine
from test_streaming import FakeAnalyzer


def item(role, text, index=None, **extra):
    return {'role': role, 'text': text, 'index': index, **extra}


def artifact(artifact_id, title, body_chars=3500):
    return f'Here you go.\n<artifact type="html" title="{title}" id="{artifact_id}">\n{"x" * body_chars}\n</artifact>\nDone.'


def file_message(name, chars, typed='see file'):
    return f'{typed}\n\n[Attached file: {name}]\n{"y" * chars}\n[End of attached file]'


def conversation():
    """first, an upload, two versions of one artifact, another artifact, filler pairs, then a new question."""
    items = [item('user', 'first question', 0), item('assistant', 'first answer', 1)]
    items += [item('user', file_message('report.pdf', 3500), 2, attachments=[{'name': 'report.pdf', 'chars': 3500}]),
              item('assistant', 'read it', 3)]
    items += [item('user', 'make a budget', 4), item('assistant', artifact('budget', 'Budget plan'), 5, real=artifact('budget', 'Budget plan'))]
    items += [item('user', 'change it', 6), item('assistant', artifact('budget', 'Budget plan'), 7, real=artifact('budget', 'Budget plan'))]
    items += [item('user', 'also a plan', 8), item('assistant', artifact('plan', 'Launch plan'), 9, real=artifact('plan', 'Launch plan'))]
    for n in range(10, 18, 2):
        items += [item('user', f'filler question {n}', n), item('assistant', f'filler answer {n}', n + 1)]
    items.append(item('user', 'the new question'))
    return items


def run(items, cap, **kw):
    return build_context('SYSTEM', items, cap=cap, context_length=200_000, out_reserve=1000, mode='t', **kw)


def texts(built):
    return '\n'.join(m['content'] for m in built['messages'])


class BuilderTests(unittest.TestCase):
    def test_estimate_is_conservative_and_counts_cjk_per_character(self):
        self.assertEqual(estimate_tokens('陳偉倫說'), 4)
        self.assertEqual(estimate_tokens('a' * 35), 10)
        self.assertEqual(estimate_tokens(''), 0)

    def test_nothing_cut_under_budget_and_no_note(self):
        items = conversation()
        built = run(items, None)
        self.assertEqual([m['content'] for m in built['messages'][1:]], [i['text'] for i in items])
        self.assertEqual(built['messages'][0]['content'], 'SYSTEM')
        self.assertFalse(any(built['info']['omitted'].values()))

    def test_cut_steps_happen_in_order(self):
        items = conversation()
        total = run(items, None)['info']['est_tokens']
        seen = []
        for cap in range(total, 0, -150):
            info = run(items, cap)['info']
            om = info['omitted']
            seen.append((bool(om['artifacts']), bool(om['files']), bool(om['messages'])))
        # Once a later step has started, every earlier step has already happened.
        first_artifact = next(i for i, s in enumerate(seen) if s[0])
        first_file = next(i for i, s in enumerate(seen) if s[1])
        first_message = next(i for i, s in enumerate(seen) if s[2])
        self.assertLess(first_artifact, first_file)
        self.assertLess(first_file, first_message)

    def test_step_one_stubs_superseded_versions_only(self):
        items = conversation()
        total = run(items, None)['info']['est_tokens']
        built = run(items, total - 300)
        om = built['info']['omitted']
        self.assertEqual([(a['id'], a['version']) for a in om['artifacts']], [('budget', 1)])
        self.assertIn('[artifact "Budget plan" id=budget v1 not included: replaced by v2]', texts(built))
        self.assertEqual(om['files'], [])
        self.assertEqual(om['messages'], [])

    def test_step_two_then_three_then_four(self):
        items = conversation()
        total = run(items, None)['info']['est_tokens']
        first = {}
        for cap in range(total, 0, -50):
            om = run(items, cap)['info']['omitted']
            for name, hit in (('v1', any((a['id'], a['version']) == ('budget', 1) for a in om['artifacts'])),
                              ('other', any((a['id'], a['version']) == ('budget', 2) for a in om['artifacts'])),
                              ('file', bool(om['files'])), ('messages', bool(om['messages']))):
                if hit and name not in first:
                    first[name] = (cap, om)
        self.assertEqual(list(first), ['v1', 'other', 'file', 'messages'])  # the order the brief fixes
        self.assertEqual(first['v1'][1]['files'], [])
        self.assertEqual(first['other'][1]['files'], [])
        self.assertEqual(first['other'][1]['messages'], [])
        self.assertEqual(first['file'][1]['messages'], [])
        self.assertIn('left out to save space', texts(run(items, first['other'][0])))
        self.assertIn('[file "report.pdf" (3,500 characters) not included]', texts(run(items, first['file'][0])))
        dropped = first['messages'][1]['messages']
        self.assertEqual(dropped, sorted(dropped))  # oldest first
        for a, b in zip(dropped[::2], dropped[1::2]):
            self.assertEqual(b, a + 1)  # user/assistant pairs

    def test_stubs_are_plain_brackets_never_artifact_tags(self):
        built = run(conversation(), 900)
        for message in built['messages']:
            for line in message['content'].splitlines():
                if 'not included' in line:
                    self.assertTrue(line.startswith('['), line)
                    self.assertNotIn('<artifact', line)

    def test_protected_items_are_never_cut(self):
        items = conversation()
        built = run(items, 1)  # absurdly small: everything that may be cut is cut
        body = texts(built)
        self.assertIn('the new question', body)          # the new message
        self.assertIn('filler question 16', body)        # the previous exchange
        self.assertIn('filler answer 16', body)
        self.assertIn('first question', body)            # a short first message
        self.assertIn('id="plan"', body)                 # latest version of the most recent artifact
        self.assertTrue(built['info']['over_cap'])
        self.assertNotIn('filler question 10', body)     # unprotected old messages are gone
        self.assertNotIn('<artifact type="html" title="Budget plan"', body)

    def test_long_first_message_is_not_protected(self):
        items = conversation()
        items[0]['text'] = 'long ' * 2000
        self.assertNotIn('long long', texts(run(items, 1)))

    def test_pins_override_the_caps(self):
        items = conversation()
        items[4]['pinned'] = True                                   # a pinned message (the budget request)
        items[2]['attachments'][0]['pinned'] = True                 # a pinned file
        built = run(items, 50, pinned_artifacts=['budget'])         # a pinned artifact: its latest version
        body = texts(built)
        self.assertIn('make a budget', body)
        self.assertIn('y' * 3500, body)
        self.assertEqual(body.count('<artifact type="html" title="Budget plan"'), 1)   # the pinned latest version (v2)
        self.assertTrue(built['info']['over_cap'])

    def test_protected_items_over_the_model_limit_are_refused(self):
        items = conversation()
        items[2]['pinned'] = True
        items[2]['text'] = 'z' * 400_000
        with self.assertRaises(ContextRefused) as caught:
            build_context('SYSTEM', items, cap=None, context_length=32_000, out_reserve=1000)
        self.assertIn('Unpin', str(caught.exception))

    def test_meeting_transcript_alone_too_big_is_named(self):
        transcript = {'role': 'user', 'text': 'w ' * 200_000, 'whole': True, 'internal': True, 'parse': False, 'name': 'transcript'}
        with self.assertRaises(ContextRefused) as caught:
            build_context('SYSTEM', [transcript], cap=None, context_length=32_000, out_reserve=1000)
        self.assertIn('transcript', str(caught.exception))

    def test_meeting_transcript_always_sent_even_in_lean(self):
        transcript = {'role': 'user', 'text': 'word ' * 12_000, 'whole': True, 'internal': True, 'parse': False, 'name': 'transcript'}
        history = [item('user', f'q{n}', n) for n in range(2, 10)]
        built = build_context('SYSTEM', [transcript, *history, item('user', 'now')], cap=cb.CAPS['lean'], context_length=200_000, out_reserve=1000)
        self.assertIn('word word word', built['messages'][1]['content'])
        self.assertTrue(built['info']['over_cap'])

    def test_unknown_context_length_is_reported(self):
        built = build_context('S', [item('user', 'hi')], cap=None, context_length=32_000, context_length_known=False, out_reserve=1000)
        self.assertFalse(built['info']['context_length_known'])
        self.assertEqual(built['info']['context_length'], 32_000)

    def test_model_limit_follows_the_brief(self):
        built = build_context('S' * 350, [item('user', 'hi')], cap=None, context_length=10_000, out_reserve=1000)
        system = cb.message_tokens('S' * 350) + cb.NOTE_RESERVE
        self.assertEqual(built['info']['model_limit'], int((10_000 - 1000) * 0.9) - system)

    def test_cap_values(self):
        self.assertEqual((cb.CAPS['lean'], cb.CAPS['balanced'], cb.CAPS['full']), (12_000, 30_000, None))

    def test_note_names_what_was_left_out_and_tells_the_model_to_ask(self):
        note = run(conversation(), 900)['messages'][0]['content']
        self.assertIn('Context note', note)
        self.assertIn('ask the user to pin it', note)
        self.assertIn('do not guess', note)


class EngineContextTests(unittest.TestCase):
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
        storage.write_json(storage.DATA / 'settings.json', {'model': 'chat/model'})
        self.privacy = PrivacyEngine(FakeAnalyzer())
        self.sent = []

        async def complete(messages, model, max_tokens=None, **kw):
            self.sent.append(messages)
            return 'ok'
        change = patch.object(engine, 'complete', complete)
        change.start()
        self.addCleanup(change.stop)

    def turn(self, session, text):
        return asyncio.run(engine.chat(session, text, self.privacy))

    def test_defaults(self):
        config = storage.settings()
        self.assertEqual((config['context_mode_chat'], config['context_mode_meeting']), ('balanced', 'full'))
        self.assertEqual(engine.context_mode({'context_mode_chat': 'bogus'}, False), 'balanced')
        self.assertEqual(engine.context_mode({}, True), 'full')

    def test_only_uncached_messages_are_masked(self):
        session = storage.new_session()
        self.turn(session, 'one')
        self.turn(session, 'two')
        calls = []
        real = self.privacy.tokenize
        with patch.object(self.privacy, 'tokenize', side_effect=lambda *a, **k: calls.append(a[0]) or real(*a, **k)):
            self.turn(session, 'three')
        self.assertEqual([c for c in calls if c in ('one', 'two')], [])  # earlier turns came from the cache
        self.assertEqual(calls.count('ok'), 1)  # the last reply is masked once, when it first goes out
        self.assertIn('three', calls)

    def test_term_added_later_is_gone_from_every_message(self):
        session = storage.new_session()
        self.turn(session, 'We met Zorblax Corp yesterday')
        self.turn(session, 'More about it')
        self.assertIn('Zorblax', json.dumps(self.sent[-1]))  # not a term yet
        storage.atomic_write(storage.TERMS, 'CLIENT: Zorblax Corp')
        self.turn(session, 'Continue please')
        self.assertNotIn('Zorblax', json.dumps(self.sent[-1]))
        self.assertIn('CLIENT__', json.dumps(self.sent[-1]))
        # Placeholders reuse the session token map, so the real value is still restorable locally.
        self.assertIn('Zorblax Corp', session['token_map'].values())

    def test_detector_setting_change_also_invalidates(self):
        session = storage.new_session()
        self.turn(session, 'hello')
        before = session['messages'][0]['masked']['fp']
        storage.write_json(storage.DATA / 'settings.json', {'model': 'chat/model', 'place_names': 'mask_all'})
        self.turn(session, 'again')
        self.assertNotEqual(session['messages'][0]['masked']['fp'], before)

    def test_cache_is_private_to_the_server(self):
        session = storage.new_session()
        self.turn(session, 'hello')
        self.assertIn('masked', storage.load_session(session['id'])['messages'][0])
        public = storage.public_session(storage.load_session(session['id']))
        self.assertNotIn('masked', json.dumps(public))
        # Web search records: masked queries, page text and masked excerpts stay on the server too.
        saved = storage.load_session(session['id'])
        saved['searches'] = [{'id': 's_1', 'planner_query_masked': 'CLIENT__1 news', 'sent_query': 'news', 'sent_with': ['m_1'],
                              'sources': [{'n': 1, 'title': 'T', 'url': 'https://a.example', 'snippet': 's',
                                           'text': 'page text', 'text_masked': 'masked page', 'label_masked': 'T — a.example'}]}]
        public = json.dumps(storage.public_session(saved))
        for field in ('masked', 'page text', 'sent_with', '"text"'):
            self.assertNotIn(field, public)
        self.assertIn('"snippet"', public)

    def test_stubs_are_never_stored(self):
        storage.write_json(storage.DATA / 'settings.json', {'model': 'chat/model', 'context_mode_chat': 'lean'})
        session = storage.new_session()
        big = file_message('report.pdf', 70_000)
        session['messages'] = [{'role': 'user', 'content': big, 'typed': 'see file', 'attachments': [{'name': 'report.pdf', 'kind': 'exact', 'chars': 70_000}]},
                               {'role': 'assistant', 'content': 'noted'}, {'role': 'user', 'content': 'q2'}, {'role': 'assistant', 'content': 'a2'},
                               {'role': 'user', 'content': 'q3'}, {'role': 'assistant', 'content': 'a3'}]
        self.turn(session, 'q4')
        self.assertIn('(70,000 characters) not included]', json.dumps(self.sent[-1]))
        self.assertNotIn('not included', json.dumps(storage.load_session(session['id'])))
        self.assertEqual(session['messages'][0]['content'], big)
        self.assertEqual(session['messages'][-1]['context']['omitted']['files'][0]['name'], 'report.pdf')

    def test_uncut_turns_store_no_context_record(self):
        session = storage.new_session()
        self.turn(session, 'hi')
        self.assertNotIn('context', session['messages'][-1])

    def test_refusal_sends_nothing_and_charges_nothing(self):
        session = storage.new_session()
        session['messages'] = [{'role': 'user', 'content': 'big ' * 30_000, 'pinned': True}, {'role': 'assistant', 'content': 'ok'}]
        with patch.object(engine, 'context_length_for', return_value=(8_000, True)), \
             patch.object(engine.monthly_budget, 'reserve', side_effect=AssertionError('must not reserve')):
            with self.assertRaises(engine.ContextTooLarge):
                self.turn(session, 'hello')
        self.assertEqual(self.sent, [])

    def test_meeting_questions_always_carry_the_full_transcript(self):
        storage.write_json(storage.DATA / 'settings.json', {'model': 'chat/model', 'context_mode_meeting': 'lean'})
        transcript = 'Priya said the budget is final. ' * 2000
        session = storage.new_session('web')
        session.update(session_type='meeting', transcript=transcript, intelligence='Summary of the meeting', mode='meeting_ready',
                       messages=[{'role': 'user', 'content': transcript}, {'role': 'assistant', 'content': 'Summary of the meeting'}])
        for n in range(8):
            asyncio.run(engine.ask_meeting(session, f'question number {n}?', self.privacy))
        sent = json.dumps(self.sent[-1])
        self.assertIn(transcript[:200].replace('\n', '\\n'), sent)
        self.assertIn('question number 7', sent)
        self.assertEqual(sum(m['content'].count('Priya said') for m in self.sent[-1]), 2000)
        self.assertIn('transcript_masked', session)

    def test_meeting_transcript_too_big_for_the_model_is_refused_before_sending(self):
        session = storage.new_session('web')
        session.update(session_type='meeting', transcript='word ' * 100_000, intelligence='s', mode='meeting_ready',
                       messages=[{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 's'}])
        with patch.object(engine, 'context_length_for', return_value=(16_000, True)):
            with self.assertRaises(engine.ContextTooLarge) as caught:
                asyncio.run(engine.ask_meeting(session, 'q?', self.privacy))
        self.assertIn('transcript', str(caught.exception))
        self.assertEqual(self.sent, [])


class PinAndBranchTests(unittest.TestCase):
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

        async def stream(messages, model, max_tokens=None):
            self.sent.append(messages)
            yield 'New reply'
            yield engine.StreamEnd('stop', {'prompt_tokens': 321})
        change = patch.object(engine, 'stream_complete', stream)
        change.start()
        self.addCleanup(change.stop)

    def make(self):
        session = storage.new_session()
        session.update(title='T', title_generated=True, token_map={}, pinned_artifacts=['budget'], messages=[
            {'role': 'user', 'content': file_message('a.pdf', 50), 'typed': 'see file', 'attachments': [{'name': 'a.pdf', 'kind': 'exact', 'chars': 50}],
             'masked': {'fp': 'x', 'c': 0, 'text': 'cached'}, 'pinned': True},
            {'role': 'assistant', 'content': 'answer', 'model': 'm'}])
        storage.save_session(session)
        return session

    def patch_pin(self, session, **pin):
        return self.client.patch('/api/sessions/' + session['id'], json={'pin': pin})

    def test_pin_endpoint_message_file_and_artifact(self):
        session = self.make()
        reply = self.patch_pin(session, kind='message', index=1, pinned=True)
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(reply.json()['messages'][1]['pinned'])
        self.assertTrue(self.patch_pin(session, kind='file', index=0, file=0, pinned=True).json()['messages'][0]['attachments'][0]['pinned'])
        self.assertEqual(self.patch_pin(session, kind='artifact', artifact='plan', pinned=True).json()['pinned_artifacts'], ['budget', 'plan'])
        self.assertEqual(self.patch_pin(session, kind='artifact', artifact='budget', pinned=False).json()['pinned_artifacts'], ['plan'])
        self.assertNotIn('pinned', self.patch_pin(session, kind='message', index=1, pinned=False).json()['messages'][1])
        self.assertEqual(self.patch_pin(session, kind='message', index=9, pinned=True).status_code, 422)
        self.assertEqual(self.patch_pin(session, kind='file', index=1, file=0, pinned=True).status_code, 422)

    def test_pins_work_in_meeting_sessions(self):
        session = storage.new_session()
        session.update(session_type='meeting', transcript='t', intelligence='i', mode='meeting_ready',
                       messages=[{'role': 'user', 'content': 't'}, {'role': 'assistant', 'content': 'i'},
                                 {'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'}])
        storage.save_session(session)
        self.assertTrue(self.patch_pin(session, kind='message', index=3, pinned=True).json()['messages'][3]['pinned'])

    def test_branch_copies_cache_and_pins(self):
        session = self.make()
        branch = self.client.post(f"/api/sessions/{session['id']}/branch", json={'message_index': 1}).json()
        saved = storage.load_session(branch['id'])
        self.assertEqual(saved['messages'][0]['masked'], {'fp': 'x', 'c': 0, 'text': 'cached'})
        self.assertTrue(saved['messages'][0]['pinned'])
        self.assertEqual(saved['pinned_artifacts'], ['budget'])
        saved['messages'][0]['masked']['text'] = 'changed in the branch'
        self.assertEqual(storage.load_session(session['id'])['messages'][0]['masked']['text'], 'cached')

    def test_edit_keeps_cache_and_pins_of_kept_messages(self):
        session = self.make()
        session['messages'] += [{'role': 'user', 'content': 'second'}, {'role': 'assistant', 'content': 'second answer'}]
        storage.save_session(session)
        reply = self.client.post('/api/chat/stream', json={'session_id': session['id'], 'message': 'edited', 'edit_index': 2})
        self.assertEqual(json.loads(reply.text.splitlines()[-1])['type'], 'done')
        saved = storage.load_session(session['id'])
        self.assertEqual([m['content'] for m in saved['messages'][2:]], ['edited', 'New reply'])
        self.assertTrue(saved['messages'][0]['pinned'])

    def test_provider_prompt_tokens_are_stored_next_to_the_estimate(self):
        session = self.make()
        self.client.post('/api/chat/stream', json={'session_id': session['id'], 'message': 'more'})
        reply = storage.load_session(session['id'])['messages'][-1]
        self.assertEqual(reply['prompt_tokens'], 321)
        self.assertGreater(reply['est_prompt_tokens'], 0)

    def test_context_modes_in_settings_api(self):
        data = self.client.get('/api/settings').json()
        self.assertEqual((data['context_mode_chat'], data['context_mode_meeting']), ('balanced', 'full'))
        self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': '', 'context_mode_chat': 'lean', 'context_mode_meeting': 'balanced'})
        data = self.client.get('/api/settings').json()
        self.assertEqual((data['context_mode_chat'], data['context_mode_meeting']), ('lean', 'balanced'))
        self.assertEqual(self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': '', 'context_mode_chat': 'huge'}).status_code, 422)

    def test_refused_stream_sends_nothing(self):
        session = self.make()
        with patch.object(engine, 'context_length_for', return_value=(2_000, True)):
            reply = self.client.post('/api/chat/stream', json={'session_id': session['id'], 'message': 'hello'})
        events = [json.loads(line) for line in reply.text.splitlines() if line.strip()]
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(self.sent, [])


if __name__ == '__main__':
    unittest.main()
