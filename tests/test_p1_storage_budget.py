"""Three focused offline checks. All persistence is isolated in temporary roots."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import dev_budget
import engine
import storage


def run_mocked(coroutine):
    """Drive immediate mocks without Windows asyncio's loopback socketpair.

    No real I/O may suspend in these tests; an unexpected yield fails the check.
    """
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('Unexpected suspension in fully mocked completion')


class StorageBudgetTests(unittest.TestCase):
    def test_budget_default_deny_durable_reservations_and_mocked_boundary(self):
        messages = [{'role': 'user', 'content': 'Private fixture body'}]
        prices = {'test/model': {'input_per_token': '0.00001', 'output_per_token': '0.00001',
                                'per_request': '0', 'valid_until': '2099-01-01T00:00:00+00:00',
                                'all_charges_known': True}}
        with tempfile.TemporaryDirectory(prefix='p1-fixture-', dir=Path(__file__).resolve().parent) as folder, patch.dict(os.environ, {}, clear=True), \
                patch.object(storage, 'DATA', Path(folder)), \
                patch.object(storage, 'openrouter_api_key', return_value='fixture-key'), \
                patch.object(engine, '_monthly_prices', (engine.time.monotonic(), [{'id': 'test/model', 'pricing': {'prompt': '0.000001', 'completion': '0.000001'}}])):
            client = AsyncMock()
            client.__aenter__.return_value = client
            client.post.return_value = httpx.Response(200,
                request=httpx.Request('POST', 'https://fixture.invalid'),
                json={'choices': [{'message': {'content': 'Mock response'}}]})
            with patch.object(engine.httpx, 'AsyncClient', return_value=client) as factory:
                with dev_budget.development_mode(prices=prices):
                    with self.assertRaises(engine.ChatError):
                        run_mocked(engine.complete(messages, 'test/model', 50))
                factory.assert_not_called()
                # Explicit development-purpose calls cannot silently act as user calls.
                with self.assertRaises(engine.ChatError):
                    run_mocked(engine.complete(messages, 'test/model', 50, purpose='development'))
                factory.assert_not_called()
                os.environ['P1_DEV_LIVE_OPT_IN'] = '1'
                with dev_budget.development_mode(prices=prices):
                    with self.assertRaises(engine.ChatError):  # missing ceiling
                        run_mocked(engine.complete(messages, 'test/model', 50))
                os.environ['P1_DEV_BUDGET_USD'] = '0.75'
                with dev_budget.development_mode(prices=prices):
                    with self.assertRaises(engine.ChatError):  # absent ledger
                        run_mocked(engine.complete(messages, 'test/model', 50))
                dev_budget.initialize()
                for price_map, maximum in ((None, 50), (prices, None)):
                    with dev_budget.development_mode(prices=price_map), self.assertRaises(engine.ChatError):
                        run_mocked(engine.complete(messages, 'test/model', maximum))
                with dev_budget.development_mode(prices=prices, historical=True), self.assertRaises(engine.ChatError):
                    run_mocked(engine.complete(messages, 'test/model', 50))
                factory.assert_not_called()
                with dev_budget.development_mode(prices=prices):
                    self.assertEqual(run_mocked(engine.complete(messages, 'test/model', 50)), 'Mock response')
                self.assertEqual(client.post.call_args.kwargs['json']['max_tokens'], 50)
                ledger_path = Path(folder) / 'dev_budget' / 'ledger.json'
                saved = ledger_path.read_text()
                self.assertNotIn('Private fixture', saved)
                self.assertNotIn('fixture-key', saved)
                self.assertEqual(len(json.loads(saved)['entries']), 1)
                # New scope retains reservations; failed requests also keep reservations.
                client.post.side_effect = httpx.ConnectError('mock failure')
                with dev_budget.development_mode(prices=prices), self.assertRaises(engine.ChatError):
                    run_mocked(engine.complete(messages, 'test/model', 50))
                self.assertEqual(len(json.loads(ledger_path.read_text())['entries']), 2)
                factory.reset_mock()
                os.environ['P1_DEV_BUDGET_USD'] = '0.00001'
                with dev_budget.development_mode(prices=prices), self.assertRaises(engine.ChatError):
                    run_mocked(engine.complete(messages, 'test/model', 50))
                factory.assert_not_called()
                os.environ['P1_DEV_BUDGET_USD'] = str(dev_budget.CAP + 1)
                with dev_budget.development_mode(prices=prices), self.assertRaises(engine.ChatError):
                    run_mocked(engine.complete(messages, 'test/model', 50))
                factory.assert_not_called()
                ledger_path.write_text('{broken', encoding='utf-8')
                os.environ['P1_DEV_BUDGET_USD'] = '0.75'
                with dev_budget.development_mode(prices=prices), self.assertRaises(engine.ChatError):
                    run_mocked(engine.complete(messages, 'test/model', 50))
                factory.assert_not_called()
                with self.assertRaises(dev_budget.BudgetError):
                    dev_budget.initialize()
                client.post.side_effect = None
                # The same mocked boundary still permits ordinary end-user requests.
                self.assertEqual(run_mocked(engine.complete(messages, 'test/model')), 'Mock response')


if __name__ == '__main__':
    unittest.main()
