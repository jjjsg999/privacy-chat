import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import adviser
import engine
import persona_store as personas
from privacy_engine import PrivacyEngine
import storage
from test_core import FakeAnalyzer
from test_p1_storage_budget import run_mocked


class AdviserChatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='p1-adviser-chat-', dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        for change in (patch.object(storage, 'DATA', Path(temporary.name) / 'data'),
                       patch.object(storage, 'settings', return_value={'model': 'test/model', 'sensitive_terms': 'CLIENT: Acme\nCLIENT: Other'}),
                       patch.object(engine.httpx, 'AsyncClient', side_effect=AssertionError('Network forbidden'))):
            change.start(); self.addCleanup(change.stop)
        self.privacy = PrivacyEngine(FakeAnalyzer())
        self.chief = 'persona_chief_of_staff'; self.career = 'persona_pfeffer_inspired'

    def test_independent_persistent_histories_persona_selection_and_latest_edit(self):
        calls = []
        async def provider(messages, model, max_tokens, purpose):
            calls.append(copy.deepcopy(messages)); return 'Synthetic advice'
        with patch.object(engine, 'complete', side_effect=provider):
            first = run_mocked(adviser.chat(self.chief, 'Acme AlphaUnique', self.privacy))
            second = run_mocked(adviser.chat(self.career, 'Other BetaUnique', self.privacy))
            self.assertNotEqual(first['id'], second['id'])
            self.assertNotIn('token_map', first)
            self.assertNotIn('AlphaUnique', json.dumps(calls[1]))
            definition = personas.get(self.chief)
            personas.save(expected_revision=0, identifier=self.chief, name=definition['name'], description=definition['description'], filename=definition['filename'], text='UPDATED PERSONA INSTRUCTIONS')
            resumed = run_mocked(adviser.chat(self.chief, 'Continue AlphaUnique', self.privacy, session_id=first['id'], expected_revision=1))
            self.assertEqual(resumed['revision'], 2)
            self.assertIn('UPDATED PERSONA INSTRUCTIONS', calls[2][0]['content'])
            self.assertNotIn('BetaUnique', json.dumps(calls[2]))
            self.assertEqual(len(resumed['messages']), 4)
            self.assertEqual(len(adviser.load_session(second['id'])['messages']), 2)
            with self.assertRaises(ValueError):
                run_mocked(adviser.chat(self.career, 'Mismatch', self.privacy, session_id=first['id'], expected_revision=2))
            with self.assertRaises(ValueError):
                run_mocked(adviser.chat(self.chief, 'Stale', self.privacy, session_id=first['id'], expected_revision=1))
            self.assertEqual(len(calls), 3)
        self.assertEqual(len(adviser.list_sessions()), 2)
        self.assertEqual(storage.list_sessions(), [])
        self.assertIn('Acme', adviser.load_session(first['id'])['token_map'].values())
        self.assertNotIn('Acme', adviser.load_session(second['id'])['token_map'].values())

    def test_blank_failure_archived_persona_and_invalid_paths_do_not_write(self):
        with patch.object(engine, 'complete', new=AsyncMock(side_effect=engine.ChatError('Mock failure'))) as provider:
            with self.assertRaises(ValueError): run_mocked(adviser.chat(self.chief, '   ', self.privacy))
            provider.assert_not_called()
            with self.assertRaises(engine.ChatError): run_mocked(adviser.chat(self.chief, 'Synthetic question', self.privacy))
        self.assertFalse((storage.DATA / 'adviser_sessions').exists())
        for identifier in ('../sessions/web_secret', 'web_abc', 'adviser_../../secret'):
            with self.assertRaises(ValueError): adviser.load_session(identifier)
        personas.archive(self.chief, 0)
        with self.assertRaises(personas.PersonaError): run_mocked(adviser.chat(self.chief, 'Question', self.privacy))
        self.assertFalse((storage.DATA / 'adviser_sessions').exists())

    def test_private_current_memory_sources_and_safe_rendering_wiring(self):
        async def provider(messages, model, max_tokens, purpose):
            self.assertNotIn('Acme', json.dumps(messages))
            self.assertNotIn('SELECTED MEMORY DATA', messages[0]['content'])
            self.assertEqual(purpose, 'development')
            return 'Recommendation for CLIENT__1.'
        with patch.object(engine, 'complete', side_effect=provider):
            session = run_mocked(adviser.chat(self.chief, 'Acme priorities?', self.privacy))
        reply = session['messages'][-1]
        self.assertEqual(reply['content'], 'Recommendation for Acme.')
        self.assertEqual(reply['sources'], [])
        html = (storage.ROOT / 'static' / 'adviser.html').read_text(encoding='utf-8')
        script = (storage.ROOT / 'static' / 'adviser.js').read_text(encoding='utf-8')
        self.assertIn('Persona Library', html)
        self.assertIn('DOMPurify.sanitize', script)
        self.assertIn('Adviser recommendation', script)
        self.assertIn('Supporting sources', script)
        self.assertIn('not real people', html)
        self.assertNotIn('setInterval', script)


if __name__ == '__main__':
    unittest.main()
