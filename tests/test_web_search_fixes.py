"""Web search fixes: placeholder instruction on every path, search status block, visible outcomes, new-chat path.
Offline: mocked provider, fake or mocked engine. Every name is invented."""
import asyncio
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import adviser
import app
import engine
import monthly_budget
import storage
import web_search
from privacy_engine import TOKEN, PrivacyEngine
from test_core import FakeAnalyzer as CoreAnalyzer
from test_p1_storage_budget import run_mocked
from test_streaming import FakeAnalyzer
from test_web_search_pipeline import CANARY, PipelineBase, plan

ROOT = Path(__file__).resolve().parent.parent
MARKER = 'The user\'s app replaced some words with privacy stand-ins'


class PromptFileTests(unittest.TestCase):
    def test_placeholder_prompt_has_no_placeholder_shaped_text(self):
        for name in ('placeholders.md', 'web_sources.md'):
            text = (ROOT / 'prompts' / name).read_text(encoding='utf-8')
            self.assertEqual(TOKEN.findall(text), [], name)
        self.assertIn(MARKER, (ROOT / 'prompts' / 'placeholders.md').read_text(encoding='utf-8'))

    def test_prompt_never_tells_the_model_to_ask_for_real_values(self):
        text = (ROOT / 'prompts' / 'placeholders.md').read_text(encoding='utf-8')
        self.assertIn('never ask the user to give the real name', text)


class EveryPathTests(PipelineBase):
    """The placeholder instruction reaches the system prompt of every model call (captured provider requests)."""

    def test_web_streaming_and_planner(self):
        session = self.new_chat()
        self.planner_replies = [plan('central bank rate decision')]
        self.send(session, 'Rates?')
        self.assertIn(MARKER, self.planner_calls[0][0]['content'])
        self.assertIn(MARKER, self.answers[-1][0]['content'])
        self.assertIn('Send as written', self.answers[-1][0]['content'])  # the web-only hint

    def captured_complete(self, reply='ok'):
        calls = []

        async def complete(messages, model, max_tokens=None, **kw):
            calls.append(copy.deepcopy(messages))
            return reply
        return calls, complete

    def test_non_streaming_web_telegram_and_titles(self):
        calls, complete = self.captured_complete()
        with patch.object(engine, 'complete', complete):
            for channel in ('web', 'telegram'):
                session = storage.new_session('telegram' if channel == 'telegram' else 'web')
                for text in ('one', 'two', 'three'):  # the third Telegram message also asks for a title
                    asyncio.run(engine.chat(session, text, self.privacy, channel=channel))
        self.assertGreaterEqual(len(calls), 7)
        for request in calls:
            self.assertIn(MARKER, request[0]['content'])
        self.assertNotIn('Send as written', calls[-1][0]['content'])  # Telegram does not get the web-only hint

    def test_meeting_analysis_title_and_questions(self):
        calls, complete = self.captured_complete('Analysis of the meeting.')
        session = storage.new_session('telegram')
        session['session_type'] = 'meeting'
        with patch.object(engine, 'complete', complete):
            asyncio.run(engine.analyze_meeting(session, 'Speaker 1: we agreed the plan.', self.privacy))
            asyncio.run(engine.ask_meeting(session, 'What was agreed?', self.privacy, 'telegram'))
        self.assertEqual(len(calls), 3)  # analysis, title, question
        for request in calls:
            self.assertIn(MARKER, request[0]['content'])
            self.assertNotIn('LITERAL__', request[0]['content'].split(MARKER)[1])


class AdviserPathTests(unittest.TestCase):
    def test_adviser_gets_the_instruction_unmasked(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for change in (patch.object(storage, 'DATA', Path(temporary.name) / 'data'),
                       patch.object(storage, 'settings', return_value={'model': 'test/model', 'sensitive_terms': 'CLIENT: Acme'})):
            change.start()
            self.addCleanup(change.stop)
        calls = []

        async def provider(messages, model, max_tokens, purpose):
            calls.append(copy.deepcopy(messages))
            return 'advice'
        with patch.object(engine, 'complete', side_effect=provider):
            run_mocked(adviser.chat('persona_chief_of_staff', 'Acme priorities?', PrivacyEngine(CoreAnalyzer())))
        system = calls[0][0]['content']
        self.assertIn(MARKER, system)
        self.assertNotIn('LITERAL__', system)


class StatusTests(PipelineBase):
    def status(self):
        content = self.answers[-1][-1]['content']
        found = re.search(r'<web_search_status>\n(.*?)\n</web_search_status>', content, re.S)
        self.assertIsNotNone(found, content)
        return found[1]

    def test_ran_public_and_generic(self):
        session = self.new_chat()
        self.planner_replies = [plan('central bank rate decision', 'CLIENT__1 freight rates outlook asia')]
        self.send(session, f'How is {CANARY} doing?')
        status = self.status()
        self.assertIn('Search 1 "central bank rate decision" ran as written (sources [1]–[2])', status)
        self.assertIn('ran without CLIENT__1 (sources [3]–[4])', status)  # the placeholder reaches the provider unchanged
        self.assertNotIn('LITERAL__', status)
        self.assertNotIn(CANARY, status)
        self.assertIn('Rerun unmasked', status)

    def test_skipped_names_what_it_needed(self):
        session = self.new_chat()
        self.planner_replies = [plan('CLIENT__1 news')]
        events = self.send(session, f'Any news on {CANARY}?')
        self.assertIn('was not run: too little was left without CLIENT__1', self.status())
        skipped = session['searches'][0]
        self.assertEqual(skipped['kind'], 'skipped')
        self.assertTrue(skipped['rerun_query'])  # the card always carries the rerun area
        self.assertIn('search_skipped', [e['type'] for e in events])

    def test_failed(self):
        session = self.new_chat()
        self.planner_replies = [plan('central bank rate decision')]

        async def broken(name, query, count=5):
            raise web_search.SearchError('HTTP 503')
        with patch.object(web_search, 'search', broken):
            self.send(session, 'Rates?')
        self.assertIn('failed (Reason: HTTP 503)', self.status())

    def test_budget(self):
        session = self.new_chat()
        with patch.object(monthly_budget, 'reserve_fixed', side_effect=monthly_budget.BudgetError('Monthly limit reached.')):
            self.send(session, 'Rates?')
        self.assertIn('monthly spending limit was reached', self.status())

    def test_no_search_needed(self):
        session = self.new_chat()
        self.planner_replies = [plan()]
        events = self.send(session, 'Thanks!')
        self.assertIn('No web search was needed', self.status())
        self.assertIn({'type': 'search_none', 'message': 'No web search needed for this message'}, events)
        self.assertEqual(session['messages'][-1]['web_note'], 'No web search needed for this message')

    def test_planner_failure(self):
        session = self.new_chat()
        self.planner_replies = ['nope', 'still nope']
        self.send(session, 'Rates?')
        self.assertIn('planner reply could not be read (Reason: invalid JSON)', self.status())

    def test_rerun_answer_carries_a_status_too(self):
        session = self.new_chat()
        self.planner_replies = [plan('CLIENT__1 price increase logistics')]
        self.send(session, f'What about {CANARY}?')
        self.stream(engine.rerun_search(session, session['searches'][0]['id'], False, self.privacy))
        self.assertIn('rerun by the user with the hidden words restored', self.status())

    def test_search_off_line(self):
        session = storage.new_session()
        session['web_search'] = False
        self.send(session, 'Please search the web for rates')
        self.assertIn(engine.WEB_SEARCH_OFF_LINE, self.answers[-1][0]['content'])
        self.assertNotIn('<web_search_status>', self.answers[-1][-1]['content'])
        storage.write_json(storage.DATA / 'settings.json', {'model': 'chat/model'})  # feature off: no line
        self.send(storage.new_session(), 'Please search the web')
        self.assertNotIn(engine.WEB_SEARCH_OFF_LINE, self.answers[-1][0]['content'])
        on = self.new_chat()
        self.settings({})
        self.send(on, 'Hello')
        self.assertNotIn(engine.WEB_SEARCH_OFF_LINE, self.answers[-1][0]['content'])


class NewChatPathTests(PipelineBase):
    """Regression: the first message of a new web chat, through the real /api/chat/stream handler."""

    def post(self, **fields):
        app.app.state.privacy = self.privacy

        async def go():
            response = await app.chat_stream(app.StreamMessage(**fields))
            return [json.loads(line) async for line in response.body_iterator]
        return asyncio.run(go())

    def test_switch_on_before_sending_runs_the_planner_and_search(self):
        self.planner_replies = [plan('latest MAS regulation')]
        events = self.post(message='conduct a web search about the latest on MAS regulation', web_search=True)
        types = [e['type'] for e in events]
        self.assertEqual(types[:2], ['start', 'planner_started'])
        self.assertIn('search_done', types)
        saved = storage.load_session(events[0]['session_id'])
        self.assertTrue(saved['web_search'])
        self.assertEqual(saved['searches'][0]['sent_query'], 'latest MAS regulation')
        self.assertIn('<web_sources>', self.answers[-1][-1]['content'])

    def test_switch_left_off_means_no_search_but_the_model_is_told(self):
        events = self.post(message='conduct a web search about the latest on MAS regulation')
        self.assertNotIn('planner_started', [e['type'] for e in events])
        self.assertEqual(self.planner_calls, [])
        self.assertIn(engine.WEB_SEARCH_OFF_LINE, self.answers[-1][0]['content'])


if __name__ == '__main__':
    unittest.main()
