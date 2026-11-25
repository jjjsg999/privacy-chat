import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Update
import engine
import storage
from privacy_engine import PrivacyEngine
from telegram_transport import TelegramTransport, allowed_ids, telegram_lifespan


class TelegramSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'sensitive_terms.txt')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)
        self.privacy = PrivacyEngine(SimpleNamespace(analyze=lambda **kwargs: []))
        self.lock = asyncio.Lock()
        self.transport = TelegramTransport(self.privacy, self.lock, {123, 456})
        self.addAsyncCleanup(self.transport.close)
        self.application = self.transport.application('123456:test-token')
        # Exercise PTB's real dispatcher, but never contact Telegram or OpenRouter.
        request = patch('telegram.request.HTTPXRequest.do_request', new=AsyncMock(return_value=(200,
            json.dumps({'ok':True, 'result':{'id':987, 'is_bot':True, 'first_name':'Test', 'username':'test_bot'}}).encode())))
        request.start()
        self.addCleanup(request.stop)
        outgoing = patch('telegram.Message.reply_text', new_callable=AsyncMock)
        self.reply = outgoing.start()
        self.addCleanup(outgoing.stop)
        self.complete = AsyncMock(return_value='CLIENT__1 reply')
        completion = patch.object(engine, 'complete', self.complete)
        completion.start()
        self.addCleanup(completion.stop)
        await self.application.initialize()
        self.addAsyncCleanup(self.application.shutdown)
        self.update_id = 0

    async def send(self, text, user_id=123, chat_type='private'):
        self.update_id += 1
        message = {'message_id':self.update_id, 'date':0, 'chat':{'id':user_id, 'type':chat_type}, 'text':text}
        if user_id is not None:
            message['from'] = {'id':user_id, 'first_name':'User', 'is_bot':False}
        if text.startswith('/'):
            message['entities'] = [{'type':'bot_command','offset':0,'length':len(text.split()[0])}]
        await self.application.process_update(Update.de_json({'update_id':self.update_id, 'message':message}, self.application.bot))

    async def test_guard_rejects_before_any_session_or_llm(self):
        with patch.object(storage, 'new_session', side_effect=AssertionError('Unauthorized session creation')):
            for text in ['/start', '/help', '/terms', '/title leak', '/new', '/meeting', '/ask question', 'Hello']:
                await self.send(text, 999)
                self.assertIn('not authorized', self.reply.call_args.args[0])
        await self.send('/start', None)
        self.assertIn('not authorized', self.reply.call_args.args[0])
        await self.send('Hello', 123, 'group')
        self.assertIn('private chat', self.reply.call_args.args[0])
        self.complete.assert_not_awaited()
        self.assertFalse(storage.DATA.exists())

    async def test_shared_engine_restart_web_continuation_and_new(self):
        storage.atomic_write(storage.TERMS, 'CLIENT: Acme')
        await self.send('/new')
        await self.send('Acme first message')
        first = storage.list_sessions()[0]['id']
        self.assertEqual(storage.load_session(first)['channel_origin'], 'telegram')
        self.assertNotIn('Acme', json.dumps(self.complete.call_args.args[0]))
        self.assertEqual(self.reply.call_args.args[0], 'Acme reply')
        # Web uses engine.chat on the same ID, then Telegram reloads that history.
        async with self.lock:
            await engine.chat(storage.load_session(first), 'Continued from Web', self.privacy)
        await self.application.shutdown()
        self.transport = TelegramTransport(self.privacy, self.lock, {123, 456})
        self.application = self.transport.application('123456:test-token')
        await self.application.initialize()
        self.addAsyncCleanup(self.application.shutdown)
        await self.send('Back in Telegram')
        self.assertEqual(len(storage.load_session(first)['messages']), 6)
        self.assertEqual(len(storage.list_sessions()), 1)
        await self.send('/new')
        await self.send('/new')
        self.assertIsNotNone(storage.load_session(first)['archived_at'])
        self.assertEqual(len(storage.list_sessions()), 1)
        self.assertEqual(storage.TERMS.read_text(), 'CLIENT: Acme')
        await self.send('Fresh content')
        mapping = json.loads((storage.DATA / 'telegram_active.json').read_text())
        second = mapping['123:123']
        self.assertNotEqual(first, second)
        self.assertEqual(storage.load_session(second)['token_map'], {})
        self.assertEqual(len(storage.load_session(second)['messages']), 2)
        await self.send('/new', 456)
        await self.send('Another user', 456)
        self.assertEqual(len(storage.list_sessions()), 3)

    async def test_commands_do_not_create_blank_chats_and_manual_title_sticks(self):
        for command in ['/start', '/help', '/terms', '/title', '/new', '/meeting', '/ask why', '/new']:
            await self.send(command)
        self.assertFalse(storage.DATA.exists())
        self.complete.assert_not_awaited()
        await self.send('A conversation')
        await self.send('/title My stable title')
        await self.send('Second turn')
        await self.send('Third turn')
        self.assertEqual(self.complete.await_count, 3)
        self.assertEqual(storage.list_sessions()[0]['title'], 'My stable title')
        await self.send('/title')
        self.assertEqual(self.reply.call_args.args[0], 'Title: My stable title')

    async def test_meeting_buffers_and_resets_five_second_timer(self):
        await self.send('/new')
        await self.send('Previous chat must not leak')
        old_id = storage.list_sessions()[0]['id']
        self.complete.reset_mock()
        storage.atomic_write(storage.TERMS, 'CLIENT: Acme')
        async def meeting_reply(messages, model):
            return re.search(r'CLIENT__\d+', messages[1]['content'])[0] + ' reply'
        self.complete.side_effect = meeting_reply
        await self.send('/meeting')
        capture = self.transport.captures['123:123']
        self.assertEqual(capture['session']['mode'], 'meeting_capture')
        self.assertEqual(capture['session']['messages'], [])
        self.assertEqual(capture['session']['token_map'], {})
        self.assertTrue(storage.load_session(old_id)['archived_at'])
        await self.send('Acme: we should delay launch.')
        first_task = capture['task']
        await asyncio.sleep(.15)
        await self.send('Finance: the budget expires Friday.')
        last_chunk_at = asyncio.get_running_loop().time()
        await asyncio.sleep(.15)
        self.assertTrue(first_task.cancelled())
        self.complete.assert_not_awaited()
        self.assertEqual(len(storage.list_sessions()), 1)
        await asyncio.wait_for(asyncio.shield(capture['task']), timeout=6)
        self.assertGreaterEqual(asyncio.get_running_loop().time() - last_chunk_at, 4.9)
        self.assertEqual(self.complete.await_count, 2)  # Intelligence plus title, never per chunk.
        outbound = json.dumps(self.complete.await_args_list[0].args[0])
        self.assertNotIn('Previous chat', outbound)
        self.assertNotIn('Acme', outbound)
        self.assertIn('budget expires Friday', outbound)
        self.assertIn('RAW MEETING TRANSCRIPT', outbound)
        saved = storage.load_session(capture['session']['id'])
        self.assertEqual(saved['transcript'], 'Acme: we should delay launch.\n\nFinance: the budget expires Friday.')
        self.assertEqual(saved['intelligence'], 'Acme reply')
        self.assertEqual(saved['mode'], 'meeting_ready')
        self.assertEqual(self.reply.call_args.args[0], 'Acme reply')

    async def test_meeting_notice_first_analysis_before_title_and_title_from_the_analysis(self):
        storage.atomic_write(storage.TERMS, 'CLIENT: Acme')
        order = []
        async def answer(messages, model):
            token = re.search(r'CLIENT__\d+', messages[1]['content'])[0]  # echo the token the request really used
            if messages[0]['content'].startswith('You name meetings'):
                order.append(('title', len(self.reply.await_args_list)))
                return token + ' launch review'
            order.append(('analysis', len(self.reply.await_args_list)))
            return token + ' should delay launch.'
        self.complete.side_effect = answer
        await self.send('/meeting')
        self.reply.reset_mock()
        with patch('telegram_transport.asyncio.sleep', new=AsyncMock()):
            await self.send('Acme: we should delay the launch by a week.')
            capture = self.transport.captures['123:123']
            await capture['task']
        replies = [call.args[0] for call in self.reply.await_args_list]
        self.assertEqual(replies[0], 'Transcript received (9 words). Analysing now; a long meeting can take a minute or two.')
        self.assertEqual(replies[1], 'Acme should delay launch.')
        self.assertEqual(order, [('analysis', 1), ('title', 2)])  # the analysis is posted before the title call
        title_request = json.dumps(self.complete.await_args_list[1].args[0])
        self.assertIn('should delay launch', title_request)       # made from the analysis...
        self.assertNotIn('RAW MEETING TRANSCRIPT', title_request)   # ...not by resending the transcript
        self.assertNotIn('Acme', title_request)
        saved = storage.load_session(capture['session']['id'])
        self.assertEqual(saved['title'], 'Acme launch review')
        self.assertTrue(saved['title_generated'])

    async def test_meeting_title_failure_keeps_the_delivered_analysis(self):
        async def answer(messages, model):
            if messages[0]['content'].startswith('You name meetings'):
                raise engine.ChatError('Unavailable')
            return 'The analysis.'
        self.complete.side_effect = answer
        await self.send('/meeting')
        with patch('telegram_transport.asyncio.sleep', new=AsyncMock()):
            await self.send('A transcript')
            capture = self.transport.captures['123:123']
            await capture['task']
        self.assertEqual(self.reply.call_args.args[0], 'The analysis.')
        saved = storage.load_session(capture['session']['id'])
        self.assertEqual((saved['title'], saved['mode']), ('Meeting intelligence', 'meeting_ready'))
        self.assertEqual(self.transport.captures, {})

    async def test_new_during_a_running_analysis_discards_it_instead_of_waiting_and_posting_it(self):
        real_sleep = asyncio.sleep
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_analysis(messages, model, *args, **kwargs):
            started.set()
            await release.wait()
            return 'Late analysis that must never be posted'
        self.complete.side_effect = slow_analysis
        await self.send('/meeting')
        with patch('telegram_transport.asyncio.sleep', new=AsyncMock()):
            await self.send('The transcript of the earlier meeting')
            await asyncio.wait_for(started.wait(), timeout=2)  # the analysis is now running and holds the lock
            self.reply.reset_mock()
            new_command = asyncio.create_task(self.send('/new'))
            await real_sleep(.1)
            self.assertTrue(new_command.done(), '/new must not wait for the running analysis')
            await new_command
        release.set()
        await real_sleep(.05)
        self.assertEqual(self.transport.captures, {})
        self.assertEqual(storage.list_sessions(), [])  # nothing from the discarded meeting was saved
        replies = [call.args[0] for call in self.reply.await_args_list]
        self.assertEqual(len(replies), 1)
        self.assertIn('Ready for a fresh conversation', replies[0])
        self.assertFalse(self.lock.locked())
        self.complete.side_effect = None
        await self.send('A normal question')  # and the chat works normally afterwards
        self.assertEqual(storage.load_session(storage.list_sessions()[0]['id'])['session_type'], 'chat')

    async def test_new_after_a_failed_analysis_discards_the_kept_chunks(self):
        self.complete.side_effect = engine.ChatError('Unavailable')
        await self.send('/meeting')
        with patch('telegram_transport.asyncio.sleep', new=AsyncMock()):
            await self.send('Transcript that fails')
            await self.transport.captures['123:123']['task']
        self.assertIn('123:123', self.transport.captures)  # kept for a retry, as designed
        self.complete.side_effect = None
        self.complete.reset_mock()
        await self.send('/new')
        self.assertEqual(self.transport.captures, {})
        self.assertIn('Ready for a fresh conversation', self.reply.call_args.args[0])
        await asyncio.sleep(.05)
        self.complete.assert_not_awaited()
    async def test_meeting_new_cancels_pending_analysis_without_blank_files(self):
        await self.send('/meeting')
        await self.send('/meeting')
        self.assertFalse(storage.DATA.exists())
        await self.send('An unfinished transcript')
        task = self.transport.captures['123:123']['task']
        await self.send('/new')
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.assertEqual(self.transport.captures, {})
        self.complete.assert_not_awaited()
        self.assertEqual(storage.list_sessions(), [])
        await self.send('Ordinary chat again')
        self.assertEqual(storage.load_session(storage.list_sessions()[0]['id'])['session_type'], 'chat')

    async def test_meeting_ask_uses_full_transcript_and_runtime_prompt(self):
        await self.send('/meeting')
        capture = self.transport.captures['123:123']
        storage.atomic_write(storage.TERMS, 'CLIENT: Acme')
        storage.write_json(storage.DATA / 'settings.json', {'model':'test/selected-meeting-model'})
        self.complete.side_effect = ['Meeting intelligence.', '""']
        await engine.analyze_meeting(capture['session'], 'Acme committed to Friday. Hidden detail: seven approvals.', self.privacy)
        self.assertEqual(capture['session']['title'], 'Meeting intelligence')
        self.assertIn('executive intelligence analyst', self.complete.await_args_list[0].args[0][0]['content'])
        self.assertTrue(all(call.args[1] == 'test/selected-meeting-model' for call in self.complete.await_args_list))
        self.complete.side_effect = None
        storage.write_json(storage.DATA / 'telegram_active.json', {'123:123':capture['session']['id']})
        self.transport.captures.clear()
        self.complete.reset_mock()
        root = storage.DATA / 'prompt-test'
        storage.atomic_write(root / 'prompts' / 'ask.md', 'Freshly edited Q&A prompt. Do not invent facts.')
        with patch.object(storage, 'ROOT', root):
            await self.send('/ask How many approvals?')
        messages = self.complete.call_args.args[0]
        self.assertEqual(self.complete.call_args.args[1], 'test/selected-meeting-model')
        self.assertIn('Freshly edited', messages[0]['content'])
        self.assertIn('seven approvals', messages[1]['content'])
        self.assertNotIn('Acme', json.dumps(messages))
        self.assertEqual(messages[-1]['content'], 'How many approvals?')
        saved = storage.load_session(capture['session']['id'])
        self.assertEqual(len(saved['messages']), 4)
        self.assertEqual(saved['transcript'], capture['session']['transcript'])

    async def test_failed_meeting_keeps_chunks_for_retry(self):
        await self.send('/meeting')
        await self.send('Keep this transcript')
        capture = self.transport.captures['123:123']
        capture['task'].cancel()
        await asyncio.gather(capture['task'], return_exceptions=True)
        self.complete.side_effect = engine.ChatError('Unavailable')
        with patch('telegram_transport.asyncio.sleep', new=AsyncMock()):
            await self.transport.finish_capture('123:123', capture, Update.de_json({
                'update_id':99, 'message':{'message_id':99,'date':0,'chat':{'id':123,'type':'private'},
                'from':{'id':123,'first_name':'User','is_bot':False},'text':'Keep this transcript'}}, self.application.bot))
        self.assertEqual(capture['chunks'], ['Keep this transcript'])
        self.assertEqual(storage.list_sessions(), [])

    async def test_failure_does_not_persist_and_long_replies_are_split(self):
        await self.send('/new')
        self.complete.side_effect = engine.ChatError('Provider unavailable')
        await self.send('Hello')
        self.assertEqual(storage.list_sessions(), [])
        self.assertFalse(storage.DATA.exists())
        self.assertEqual(self.reply.call_args.args[0], 'Provider unavailable')
        self.complete.side_effect = None
        self.complete.return_value = '😀' * 4100
        self.reply.reset_mock()
        await self.send('Try again')
        chunks = [call.args[0] for call in self.reply.await_args_list]
        self.assertEqual(''.join(chunks), self.complete.return_value)
        self.assertTrue(all(len(chunk.encode('utf-16-le')) // 2 <= 4000 for chunk in chunks))

    async def test_default_capture_and_start_meeting(self):
        # A first message needs no /meeting command and makes no immediate LLM call.
        await self.send('First transcript chunk')
        capture = self.transport.captures['123:123']
        self.assertEqual(capture['chunks'], ['First transcript chunk'])
        self.complete.assert_not_awaited()
        self.assertEqual(storage.list_sessions(), [])
        await self.send('/new')
        await self.send('Normal chat')
        old_id = storage.list_sessions()[0]['id']
        await self.send('/start')
        self.assertTrue(storage.load_session(old_id)['archived_at'])
        fresh = self.transport.captures['123:123']
        self.assertEqual(fresh['chunks'], [])
        self.assertEqual(fresh['session']['messages'], [])
        self.assertEqual(len(storage.list_sessions()), 1)
        with patch('telegram_transport.asyncio.sleep', new=AsyncMock()):
            await self.send('New meeting transcript')
            await fresh['task']
        saved = storage.load_session(fresh['session']['id'])
        self.assertEqual(saved['transcript'], 'New meeting transcript')
        self.assertEqual(saved['mode'], 'meeting_ready')

    async def test_meeting_diagnosis_records_exact_request_and_toggle(self):
        storage.write_json(storage.DATA / 'settings.json', {'model': 'test/model', 'chat_diagnosis': True})
        storage.atomic_write(storage.TERMS, 'CLIENT: Acme')
        session = storage.new_session('telegram')
        session['session_type'] = 'meeting'
        result = await engine.analyze_meeting(session, 'Acme approved the plan.', self.privacy)
        snapshot = result['meeting_api_request']
        self.assertEqual(snapshot['messages'], self.complete.await_args_list[0].args[0])
        self.assertEqual(snapshot['model'], 'test/model')
        self.assertNotIn('Acme', json.dumps(snapshot))
        self.assertRegex(snapshot['messages'][1]['content'], r'CLIENT__\d+ approved')
        self.assertNotIn('token_map', result)
        storage.atomic_write(storage.TERMS, 'SYSTEM: plan')
        await engine.ask_meeting(session, 'What happened?', self.privacy)
        self.assertEqual(storage.load_session(session['id'])['meeting_api_request'], snapshot)
        storage.write_json(storage.DATA / 'settings.json', {'model': 'test/model', 'chat_diagnosis': False})
        self.assertNotIn('meeting_api_request', storage.public_session(session))
        other = storage.new_session('telegram')
        await engine.analyze_meeting(other, 'Another meeting', self.privacy)
        self.assertNotIn('meeting_api_request', storage.load_session(other['id']))

    async def test_config_and_polling_lifecycle(self):
        self.assertEqual(allowed_ids('123, 456\n789'), {123,456,789})
        for value in ['', '123,typo', '-123', '0']:
            with self.assertRaises(ValueError):
                allowed_ids(value)
        events = []
        def record(name):
            async def callback(**kwargs):
                events.append(name)
            return AsyncMock(side_effect=callback)
        fake = SimpleNamespace(initialize=record('initialize'), start=record('start'),
            stop=record('stop'), shutdown=record('shutdown'),
            updater=SimpleNamespace(start_polling=record('poll'), stop=record('stop_poll')))
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN':'test', 'TELEGRAM_ALLOWED_USER_IDS':'123'}), patch.object(TelegramTransport, 'application', return_value=fake):
            async with telegram_lifespan(self.privacy, self.lock):
                self.assertEqual(events, ['initialize', 'start', 'poll'])
        self.assertEqual(events, ['initialize','start','poll','stop_poll','stop','shutdown'])
        self.assertEqual(fake.updater.start_polling.call_args.kwargs['timeout'], 30)
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN':''}), patch.object(TelegramTransport, 'application') as build:
            async with telegram_lifespan(self.privacy, self.lock):
                pass
            build.assert_not_called()


if __name__ == '__main__':
    unittest.main()

