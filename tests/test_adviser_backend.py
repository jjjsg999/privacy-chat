import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import adviser
import engine
import persona_store as personas
from privacy_engine import PrivacyEngine
import storage
from test_core import FakeAnalyzer
from test_p1_storage_budget import run_mocked


class AdviserBackendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='p1-adviser-', dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        for change in (patch.object(storage, 'DATA', Path(temporary.name) / 'data'),
                       patch.object(storage, 'settings', return_value={'model': 'test/model', 'sensitive_terms': 'CLIENT: Acme'}),
                       patch.object(engine.httpx, 'AsyncClient', side_effect=AssertionError('Network forbidden'))):
            change.start(); self.addCleanup(change.stop)

    def test_persona_safe_import_edit_archive_and_seed_labels(self):
        initial = personas.load(); self.assertEqual(len(initial['personas']), 2)
        self.assertFalse(storage.DATA.exists())
        self.assertIn('not the real person', initial['personas']['persona_pfeffer_inspired']['text'])
        for filename in ('../bad.md', 'C:\\bad.txt', 'payload.exe', 'payload.exe.md', 'NUL.txt', '.hidden.md'):
            with self.assertRaises(personas.PersonaError):
                personas.save(expected_revision=0, name='Test', description='', filename=filename, text='Text')
        for text in ('\x00binary', 'x' * 20001, '\ud800'):
            with self.assertRaises(personas.PersonaError):
                personas.save(expected_revision=0, name='Test', description='', filename='safe.md', text=text)
        self.assertFalse(storage.DATA.exists())
        result = personas.save(expected_revision=0, name='Test adviser', description='Synthetic', filename='safe.md', text='On request advice')
        sid = result['persona']['id']
        result = personas.save(expected_revision=result['revision'], identifier=sid, name='Edited', description='Synthetic', filename='safe.txt', text='Edited instructions')
        self.assertEqual(personas.get(sid)['text'], 'Edited instructions')
        self.assertEqual(personas.get(sid)['revision'], 2)
        with self.assertRaises(personas.PersonaError):
            personas.save(expected_revision=0, identifier=sid, name='Stale', description='', filename='safe.md', text='Stale')
        personas.archive(sid, result['revision'])
        with self.assertRaises(personas.PersonaError): personas.get(sid)
        self.assertTrue(personas.load()['personas'][sid]['archived'])

    def test_retired_retrieval_returns_no_context(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('No memory reads')):
            self.assertEqual(adviser.retrieve('anything')['items'], [])

    def test_private_composition_and_no_unintended_memory_mutation(self):
        persona = personas.save(expected_revision=0, name='Private adviser', description='', filename='adviser.md', text='Help Acme. Contact jane@example.com only if asked; do not send messages.')['persona']
        calls = []
        async def provider(messages, model, max_tokens, purpose):
            calls.append(messages); payload = json.dumps(messages)
            self.assertNotIn('Acme', payload); self.assertNotIn('jane@example.com', payload)
            self.assertIn('YOUR recommendations', payload)
            self.assertNotIn('SELECTED MEMORY DATA', payload)
            self.assertEqual(purpose, 'development')
            return 'Consider CLIENT__1 priorities.'
        with patch.object(engine, 'complete', side_effect=provider):
            result = run_mocked(adviser.answer(persona['id'], 'Acme priorities?', PrivacyEngine(FakeAnalyzer()), {}))
        self.assertEqual(len(calls), 1)
        self.assertIn('Acme', result['content'])
        self.assertFalse((storage.DATA / 'sessions').exists())


if __name__ == '__main__':
    unittest.main()
