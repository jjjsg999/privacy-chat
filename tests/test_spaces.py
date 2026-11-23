"""Spaces: grouping chats for organising only. Temporary folders and invented chats; the model is mocked."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import engine
import storage
from privacy_engine import PrivacyEngine
from test_streaming import FakeAnalyzer


class SpacesTestCase(unittest.TestCase):
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

        async def stream(messages, model, max_tokens=None):
            yield 'A reply'
        change = patch.object(engine, 'stream_complete', stream)
        change.start()
        self.addCleanup(change.stop)

    def make(self, title='A chat', space=..., **extra):
        """Saves an invented chat directly. space=... leaves the field out, like a chat from before spaces."""
        session = storage.new_session()
        session.update(title=title, title_generated=True, messages=[{'role': 'user', 'content': f'hello from {title}'},
                       {'role': 'assistant', 'content': 'hi', 'model': 'm'}], **extra)
        if space is ...:
            del session['space']
        else:
            session['space'] = space
        path = storage.session_path(session['id'])
        storage.write_json(path, session)  # written as is: no stamping
        return session

    def raw(self, session):
        return json.loads(storage.session_path(session['id']).read_text(encoding='utf-8'))

    def titles(self, **query):
        return sorted(s['title'] for s in self.client.get('/api/sessions', params=query).json())

    def spaces(self):
        return {s['id']: s for s in self.client.get('/api/spaces').json()['spaces']}


class DefaultsAndLegacyTests(SpacesTestCase):
    def test_two_spaces_by_default_and_telegram_goes_to_work(self):
        body = self.client.get('/api/spaces').json()
        self.assertEqual([(s['id'], s['name']) for s in body['spaces']], [('work', 'Work'), ('personal', 'Personal')])
        self.assertEqual(body['telegram_space'], 'work')
        settings = self.client.get('/api/settings').json()
        self.assertEqual((settings['spaces'][0]['id'], settings['telegram_space']), ('work', 'work'))

    def test_a_chat_with_no_space_field_is_in_work_without_rewriting_the_file(self):
        old = self.make('Old chat')
        self.assertNotIn('space', self.raw(old))
        self.assertEqual(self.titles(space='work'), ['Old chat'])
        self.assertEqual(self.titles(space='personal'), [])
        self.assertEqual(self.client.get('/api/sessions/' + old['id']).json()['space'], 'work')
        self.client.get('/api/sessions')
        self.assertNotIn('space', self.raw(old))  # reading never rewrites a session file

    def test_the_field_is_written_the_next_time_the_chat_is_saved_for_any_reason(self):
        for change in (lambda s: self.client.patch('/api/sessions/' + s['id'], json={'title': 'Renamed'}),
                       lambda s: self.client.patch('/api/sessions/' + s['id'], json={'pinned': True}),
                       lambda s: self.client.patch('/api/sessions/' + s['id'], json={'pin': {'kind': 'message', 'index': 0, 'pinned': True}}),
                       lambda s: self.client.post('/api/chat/stream', json={'session_id': s['id'], 'message': 'more'})):
            old = self.make('Old chat')
            self.assertNotIn('space', self.raw(old))
            change(old)
            self.assertEqual(self.raw(old)['space'], 'work')

    def test_an_unknown_space_id_falls_back_to_work(self):
        lost = self.make('Lost chat', space='deleted_space')
        self.assertEqual(self.titles(space='work'), ['Lost chat'])
        self.assertEqual(self.client.get('/api/sessions/' + lost['id']).json()['space'], 'work')
        self.assertEqual(self.spaces()['work']['chats'], 1)
        self.assertEqual(self.client.get('/api/sessions', params={'space': 'nope'}).status_code, 422)

    def test_legacy_chats_count_toward_work_and_keep_it_from_being_deleted(self):
        self.make('Old chat')
        self.client.put('/api/spaces/telegram', json={'space': 'personal'})  # so the chat count is the reason shown
        self.assertEqual(self.spaces()['work']['chats'], 1)
        self.assertIn('1 chat', self.spaces()['work']['delete_blocked'])


class NewChatsAndMovingTests(SpacesTestCase):
    def stream(self, **body):
        response = self.client.post('/api/chat/stream', json={'message': 'Hello', **body})
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        return events[0]['session_id']

    def test_new_chats_take_the_current_space(self):
        personal = self.stream(space='personal')
        default = self.stream()
        unknown = self.stream(space='nonsense')
        self.assertEqual([storage.load_session(i)['space'] for i in (personal, default, unknown)], ['personal', 'work', 'work'])
        async def complete(messages, model, *args, **kwargs):
            return 'ok'
        with patch.object(engine, 'complete', complete):  # the non-streaming route too
            reply = self.client.post('/api/chat', json={'message': 'Hello', 'space': 'personal'})
        self.assertEqual((reply.status_code, reply.json()['space']), (200, 'personal'))

    def test_a_follow_up_message_does_not_change_the_space(self):
        first = self.stream(space='personal')
        self.client.post('/api/chat/stream', json={'session_id': first, 'message': 'again', 'space': 'work'})
        self.assertEqual(storage.load_session(first)['space'], 'personal')

    def test_move_to_another_space_and_list_by_space(self):
        chat = self.make('Move me', space='work')
        other = self.make('Stay put', space='work')
        reply = self.client.patch('/api/sessions/' + chat['id'], json={'space': 'personal'})
        self.assertEqual((reply.status_code, reply.json()['space']), (200, 'personal'))
        self.assertEqual(self.titles(space='personal'), ['Move me'])
        self.assertEqual(self.titles(space='work'), ['Stay put'])
        self.assertEqual(self.titles(space='all'), ['Move me', 'Stay put'])
        self.assertEqual(self.titles(), ['Move me', 'Stay put'])  # no filter means every space, as before
        self.assertEqual(self.client.patch('/api/sessions/' + chat['id'], json={'space': 'nope'}).status_code, 422)
        self.assertEqual(self.raw(chat)['space'], 'personal')

    def test_moving_keeps_everything_else_and_the_list_order(self):
        chat = self.make('Move me', space='work', pinned=True, token_map={'PERSON__1': 'Ann'})
        before = self.raw(chat)
        self.client.patch('/api/sessions/' + chat['id'], json={'space': 'personal'})
        after = self.raw(chat)
        self.assertEqual({k: v for k, v in after.items() if k != 'space'}, {k: v for k, v in before.items() if k != 'space'})

    def test_branching_keeps_the_original_chats_space_and_its_own_token_map(self):
        chat = self.make('Branch me', space='personal', token_map={'PERSON__1': 'Ann'})
        branch = self.client.post(f"/api/sessions/{chat['id']}/branch", json={'message_index': 1}).json()
        saved = storage.load_session(branch['id'])
        self.assertEqual(saved['space'], 'personal')
        self.assertEqual(saved['token_map'], {'PERSON__1': 'Ann'})
        saved['token_map']['PERSON__2'] = 'Bob'
        self.assertNotIn('PERSON__2', storage.load_session(chat['id'])['token_map'])  # nothing is shared
        legacy = self.make('Old chat')
        legacy_branch = self.client.post(f"/api/sessions/{legacy['id']}/branch", json={'message_index': 1}).json()
        self.assertEqual(storage.load_session(legacy_branch['id'])['space'], 'work')


class NameAndDeleteRuleTests(SpacesTestCase):
    def add(self, name):
        return self.client.post('/api/spaces', json={'name': name})

    def test_add_trims_and_ids_are_stable_across_rename(self):
        reply = self.add('  Side projects  ')
        self.assertEqual(reply.status_code, 200)
        added = [s for s in reply.json()['spaces'] if s['name'] == 'Side projects'][0]
        renamed = self.client.patch('/api/spaces/' + added['id'], json={'name': 'Hobbies'}).json()
        self.assertEqual([s['id'] for s in renamed['spaces']][-1], added['id'])
        self.assertEqual([s['name'] for s in renamed['spaces']][-1], 'Hobbies')
        chat = self.make('Mine', space=added['id'])
        self.client.patch('/api/spaces/' + added['id'], json={'name': 'Hobbies and more'})
        self.assertEqual(self.titles(space=added['id']), ['Mine'])  # the chat followed the id, not the name

    def test_name_rules(self):
        self.assertEqual(self.add('').status_code, 422)
        self.assertEqual(self.add('   ').status_code, 422)
        self.assertEqual(self.add('x' * 41).status_code, 422)
        self.assertEqual(self.add('x' * 40).status_code, 200)
        self.assertEqual(self.add('work').status_code, 409)       # unique, ignoring capitals
        self.assertEqual(self.add('  PERSONAL ').status_code, 409)
        self.assertEqual(self.add('Work').status_code, 409)
        body = self.client.patch('/api/spaces/personal', json={'name': 'work'})
        self.assertEqual(body.status_code, 409)
        self.assertEqual(self.client.patch('/api/spaces/personal', json={'name': 'PERSONAL'}).status_code, 200)  # its own name, new capitals
        self.assertEqual(self.client.patch('/api/spaces/nope', json={'name': 'Anything'}).status_code, 404)
        self.assertEqual(self.spaces()['personal']['name'], 'PERSONAL')

    def test_delete_is_refused_while_the_space_has_chats_archived_included(self):
        self.add('Extra')
        extra = [s for s in self.client.get('/api/spaces').json()['spaces'] if s['name'] == 'Extra'][0]['id']
        archived = self.make('Hidden', space=extra, archived=True)
        reply = self.client.delete('/api/spaces/' + extra)
        self.assertEqual(reply.status_code, 409)
        self.assertIn('1 chat', reply.json()['detail'])
        self.assertIn('archived', reply.json()['detail'])
        self.assertIn('1 chat', self.spaces()[extra]['delete_blocked'])
        storage.delete_session(archived['id'])
        self.assertIsNone(self.spaces()[extra]['delete_blocked'])
        self.assertEqual(self.client.delete('/api/spaces/' + extra).status_code, 200)
        self.assertNotIn(extra, self.spaces())

    def test_the_last_space_cannot_be_deleted(self):
        self.client.delete('/api/spaces/personal')
        self.assertEqual(list(self.spaces()), ['work'])
        self.assertEqual(self.client.delete('/api/spaces/work').status_code, 409)
        self.assertIn('last space', self.spaces()['work']['delete_blocked'])

    def test_the_telegram_space_cannot_be_deleted_until_it_is_changed(self):
        self.client.put('/api/spaces/telegram', json={'space': 'personal'})
        reply = self.client.delete('/api/spaces/personal')
        self.assertEqual(reply.status_code, 409)
        self.assertIn('Telegram', reply.json()['detail'])
        self.assertIn('Telegram', self.spaces()['personal']['delete_blocked'])
        self.client.put('/api/spaces/telegram', json={'space': 'work'})
        self.assertEqual(self.client.delete('/api/spaces/personal').status_code, 200)

    def test_deleting_something_that_is_not_there_and_bad_telegram_choices(self):
        self.assertEqual(self.client.delete('/api/spaces/nope').status_code, 404)
        self.assertEqual(self.client.put('/api/spaces/telegram', json={'space': 'nope'}).status_code, 404)

    def test_spaces_survive_saving_the_main_settings(self):
        self.add('Extra')
        self.client.put('/api/settings', json={'model': 'chat/model', 'sensitive_terms': 'CLIENT: Acme'})
        self.assertEqual([s['name'] for s in self.client.get('/api/spaces').json()['spaces']], ['Work', 'Personal', 'Extra'])

    def test_a_first_write_with_no_settings_file_still_leaves_a_model(self):
        (storage.DATA / 'settings.json').unlink()
        self.add('Extra')
        self.assertTrue(storage.settings()['model'])


class SearchTests(SpacesTestCase):
    def test_search_covers_the_chosen_space_unless_all_spaces_is_asked_for(self):
        self.make('Budget at work', space='work')
        self.make('Budget at home', space='personal')
        self.assertEqual(self.titles(space='work', q='budget'), ['Budget at work'])
        self.assertEqual(self.titles(space='personal', q='BUDGET'), ['Budget at home'])
        everywhere = self.client.get('/api/sessions', params={'space': 'all', 'q': 'budget'}).json()
        self.assertEqual({(s['title'], s['space']) for s in everywhere}, {('Budget at work', 'work'), ('Budget at home', 'personal')})

    def test_pinned_and_archived_lists_are_per_space(self):
        self.make('Pinned work', space='work', pinned=True)
        self.make('Archived work', space='work', archived=True)
        self.make('Pinned home', space='personal', pinned=True)
        rows = self.client.get('/api/sessions', params={'space': 'personal'}).json()
        self.assertEqual([(r['title'], r['pinned'], r['archived']) for r in rows], [('Pinned home', True, False)])


class TelegramSpaceTests(SpacesTestCase):
    def test_telegram_chats_and_meetings_go_to_the_telegram_space(self):
        self.assertEqual(storage.new_session('telegram')['space'], 'work')
        self.client.put('/api/spaces/telegram', json={'space': 'personal'})
        chat = storage.new_session('telegram')
        self.assertEqual(chat['space'], 'personal')
        self.assertEqual(storage.new_session('telegram', space='work')['space'], 'personal')  # the argument cannot override it
        self.assertEqual(storage.new_session('web')['space'], 'work')
        from telegram_transport import TelegramTransport
        transport = TelegramTransport(PrivacyEngine(FakeAnalyzer()), asyncio.Lock(), {1})
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=1), effective_user=SimpleNamespace(id=1))
        transport.begin_capture('1:1', update)
        self.assertEqual(transport.captures['1:1']['session']['space'], 'personal')

    def test_a_saved_telegram_meeting_lands_in_that_space_and_is_listed_there(self):
        self.client.put('/api/spaces/telegram', json={'space': 'personal'})
        session = storage.new_session('telegram')
        session.update(session_type='meeting', transcript='t', intelligence='i', mode='meeting_ready', title='Standup',
                       messages=[{'role': 'user', 'content': 't'}, {'role': 'assistant', 'content': 'i'}])
        storage.save_session(session)
        self.assertEqual(self.titles(space='personal'), ['Standup'])
        self.assertEqual(self.titles(space='work'), [])

    def test_changing_the_telegram_space_does_not_move_existing_chats(self):
        old = storage.new_session('telegram')
        old.update(title='Earlier', messages=[{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'y'}])
        storage.save_session(old)
        self.client.put('/api/spaces/telegram', json={'space': 'personal'})
        self.assertEqual(self.titles(space='work'), ['Earlier'])


class PrivacyUnchangedTests(SpacesTestCase):
    def test_token_maps_stay_one_per_chat_in_every_space(self):
        privacy = PrivacyEngine(FakeAnalyzer())
        storage.atomic_write(storage.TERMS, 'CLIENT: Acme')
        sessions = [storage.new_session(space=s) for s in ('work', 'personal')]

        async def complete(messages, model, *args, **kwargs):
            self.assertNotIn('Acme', json.dumps(messages))
            return 'ok'
        with patch.object(engine, 'complete', complete):
            for session in sessions:
                asyncio.run(engine.chat(session, 'Acme news', privacy))
        self.assertEqual([s['token_map'] for s in sessions], [{'CLIENT__1': 'Acme'}, {'CLIENT__1': 'Acme'}])
        self.assertIsNot(sessions[0]['token_map'], sessions[1]['token_map'])


class PagesTests(SpacesTestCase):
    def test_the_switcher_and_settings_section_are_in_the_pages(self):
        home = self.client.get('/').text
        self.assertIn('id="space-switcher"', home)
        self.assertIn('id="all-spaces"', home)
        settings = self.client.get('/settings').text
        self.assertIn('id="spaces-card"', settings)
        self.assertIn('spaces-settings.js', settings)
        self.assertIn('id="telegram-space"', settings)
        self.assertEqual(self.client.get('/static/spaces-settings.js').status_code, 200)
        self.assertNotIn('style="', home + settings)  # the page policy forbids inline styles


if __name__ == '__main__':
    unittest.main()
