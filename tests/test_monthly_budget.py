import asyncio
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import app
import engine
import monthly_budget as budget
import storage
from test_p1_storage_budget import run_mocked as run


class MonthlyBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'terms.txt'), ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value); change.start(); self.addCleanup(change.stop)
        self.messages = [{'role': 'user', 'content': 'Private synthetic text'}]

    def settings(self, limit):
        storage.write_json(storage.DATA / 'settings.json', {'model': 'test/model', 'monthly_cost_ceiling_usd': str(limit)})

    def test_reserve_settle_restart_rollover_and_corruption(self):
        self.settings('.02'); rates = (Decimal(0), Decimal('.001'), Decimal(0))
        with patch.object(budget, 'period', return_value='2026-10'):
            first = budget.reserve(self.messages, 10, rates)
            with self.assertRaises(budget.BudgetError): budget.reserve(self.messages, 10, rates)
            budget.settle(first, {'cost': '.003'})
            budget.settle(first, {'cost': '.001'})  # Settlement is idempotent.
            second = budget.reserve(self.messages, 10, rates)
            self.assertEqual(Decimal(budget.status()['used_usd']), Decimal('.014'))
            budget.settle(second, {})  # Unknown cost cannot release funds.
            self.assertEqual(Decimal(budget.status()['reserved_usd']), Decimal('.011'))
            self.settings('.001')
            with self.assertRaises(budget.BudgetError): budget.reserve(self.messages, 10, rates)
        self.settings('.02')
        with patch.object(budget, 'period', return_value='2026-11'):
            self.assertEqual(budget.status()['used_usd'], '0')
            budget.reserve(self.messages, 10, rates)
        ledger = budget._root() / 'ledger.json'
        self.assertEqual(len(json.loads(ledger.read_text())['entries']), 3)
        ledger.unlink()
        with self.assertRaises(budget.BudgetError): budget.reserve(self.messages, 10, rates)
        self.assertFalse(budget.status()['available'])

    def test_shared_completion_bounds_settlement_and_refusal_before_post(self):
        self.settings('2')
        catalogue = [{'id': 'test/model', 'pricing': {'prompt': '.000001', 'completion': '.000002'}}]
        client = AsyncMock(); client.__aenter__.return_value = client
        client.post.return_value = httpx.Response(200, request=httpx.Request('POST', 'https://fixture.invalid'),
            json={'choices': [{'message': {'content': 'Mock reply'}}], 'usage': {'cost': '.002'}})
        with patch.object(storage, 'openrouter_api_key', return_value='fixture-key'), \
             patch.object(engine, '_monthly_prices', None), \
             patch.object(engine, 'model_catalogue', new=AsyncMock(return_value=catalogue)), \
             patch.object(engine.httpx, 'AsyncClient', return_value=client):
            self.assertEqual(run(engine.complete(self.messages, 'test/model')), 'Mock reply')
            payload = client.post.call_args.kwargs['json']
            self.assertEqual(payload['max_tokens'], 6000)
            self.assertEqual(payload['provider']['max_price']['completion'], 2)
            self.assertEqual(Decimal(budget.status()['used_usd']), Decimal('.002'))
            self.settings('0')
            with self.assertRaisesRegex(engine.ChatError, 'Monthly cost ceiling'):
                run(engine.complete(self.messages, 'test/model'))
            self.settings('2')
            with self.assertRaisesRegex(engine.ChatError, 'pricing'):
                run(engine.complete(self.messages, 'unknown/model'))
            self.assertEqual(client.post.await_count, 1)
            client.post.side_effect = httpx.ConnectError('Mock failure')
            with self.assertRaises(engine.ChatError): run(engine.complete(self.messages, 'test/model'))
            self.assertGreater(Decimal(budget.status()['reserved_usd']), 0)
        text = (budget._root() / 'ledger.json').read_text()
        self.assertNotIn('Private synthetic text', text); self.assertNotIn('fixture-key', text)

    def test_settings_default_save_favourites_preserve_usage_and_validation(self):
        with patch.object(app, 'chat_lock', asyncio.Lock()), patch.object(storage, 'openrouter_api_key', return_value=''):
            self.assertEqual(run(app.get_settings())['monthly_cost_ceiling_usd'], '2.00')
            result = run(app.put_settings(app.Settings(model='test/model', sensitive_terms='', monthly_cost_ceiling_usd='5.25')))
            self.assertEqual(result['monthly_cost_ceiling_usd'], '5.25')
            budget.reserve(self.messages, 10, (Decimal(0), Decimal('.001'), Decimal(0)))
            before = budget.status()['used_usd']
            run(app.favourite(app.Favourite(model='test/model', favourite=True)))
            self.assertEqual(storage.settings()['monthly_cost_ceiling_usd'], '5.25')
            run(app.put_settings(app.Settings(model='test/model', sensitive_terms='')))
            self.assertEqual(storage.settings()['monthly_cost_ceiling_usd'], '5.25')
            self.assertEqual(budget.status()['used_usd'], before)
            for invalid in ('-1', 'NaN', 'Infinity', '1.001'):
                with self.assertRaises(ValueError): app.Settings(model='test/model', sensitive_terms='', monthly_cost_ceiling_usd=invalid)
