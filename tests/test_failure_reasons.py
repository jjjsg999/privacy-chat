"""Meeting failures name their kind (timeout, empty reply, HTTP status) and get a longer timeout. Everything is mocked."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

import engine
import storage
from privacy_engine import PrivacyEngine
from test_streaming import FakeAnalyzer


class FailureReasonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'terms.txt'), ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)
        storage.write_json(storage.DATA / 'settings.json', {'model': 'test/model'})
        for change in (patch.object(storage, 'openrouter_api_key', return_value='fixture-key'),
                       patch.object(engine, '_monthly_prices', (engine.time.monotonic(),
                                    [{'id': 'test/model', 'pricing': {'prompt': '0', 'completion': '0'}}]))):
            change.start()
            self.addCleanup(change.stop)
        self.timeouts = []

    def call(self, outcome, wrapper=None):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = outcome if isinstance(outcome, Exception) else None
        if not isinstance(outcome, Exception):
            client.post.return_value = outcome

        def factory(**kwargs):
            self.timeouts.append(kwargs.get('timeout'))
            return client
        with patch.object(engine.httpx, 'AsyncClient', side_effect=factory):
            coroutine = engine.complete([{'role': 'user', 'content': 'hi'}], 'test/model', 50) if wrapper is None else wrapper()
            return asyncio.run(coroutine)

    def error(self, outcome, wrapper=None):
        with self.assertRaises(engine.ChatError) as caught:
            self.call(outcome, wrapper)
        return str(caught.exception)

    def test_each_kind_of_failure_is_named_without_leaking_provider_text(self):
        request = httpx.Request('POST', 'https://fixture.invalid')
        self.assertIn('took too long', self.error(httpx.ReadTimeout('secret-ish detail', request=request)))
        self.assertIn('empty reply', self.error(httpx.Response(200, request=request, json={'choices': [{'message': {'content': '  '}}]})))
        message = self.error(httpx.Response(429, request=request, json={'error': 'secret provider text'}))
        self.assertIn('HTTP 429', message)
        self.assertNotIn('secret', message)
        self.assertIn('OpenRouter request failed', self.error(httpx.ConnectError('boom', request=request)))

    def test_meeting_calls_wait_longer_than_ordinary_calls(self):
        request = httpx.Request('POST', 'https://fixture.invalid')
        ok = httpx.Response(200, request=request, json={'choices': [{'message': {'content': 'fine'}}]})
        self.call(ok)
        config = storage.settings()
        session = storage.new_session()
        self.call(ok, wrapper=lambda: engine.send_meeting(session, [{'role': 'user', 'content': 'hi'}], config))
        self.assertEqual(self.timeouts, [90, engine.SLOW_TIMEOUT])
        self.call(ok)
        self.assertEqual(self.timeouts[-1], 90)  # the longer timeout does not stick to later calls

    def test_telegram_failure_notice_includes_the_safe_reason(self):
        from telegram_transport import TelegramTransport
        transport = TelegramTransport(PrivacyEngine(FakeAnalyzer()), asyncio.Lock(), {1})
        sent = []
        message = type('M', (), {'reply_text': AsyncMock(side_effect=lambda text, **kw: sent.append(text))})()
        update = type('U', (), {'effective_message': message, 'effective_chat': type('C', (), {'id': 1})(),
                                'effective_user': type('P', (), {'id': 1})()})()
        capture = {'session': storage.new_session('telegram'), 'chunks': ['x'], 'task': None}
        transport.captures['1:1'] = capture
        failing = AsyncMock(side_effect=engine.ChatError('The model took too long to answer and the request timed out.'))
        with patch('telegram_transport.asyncio.sleep', new=AsyncMock()), patch.object(engine, 'analyze_meeting', failing):
            asyncio.run(transport.finish_capture('1:1', capture, update))
        self.assertIn('Transcript received (1 words)', sent[0])  # the notice comes first
        self.assertIn('Meeting processing or delivery failed', sent[-1])
        self.assertIn('Reason: The model took too long', sent[-1])
        self.assertIn('1:1', transport.captures)  # chunks stay for a retry


if __name__ == '__main__':
    unittest.main()
