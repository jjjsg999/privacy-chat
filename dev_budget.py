"""Explicit development/backfill scope; ordinary end-user calls are unaffected.

Before an authorized live experiment, initialize the ledger once, set
P1_DEV_LIVE_OPT_IN=1 and P1_DEV_BUDGET_USD (<=2.00), and supply verified prices
to development_mode(). Initialization is never implicit in a completion.
Reservations are never refunded, including failed/ambiguous provider requests.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from datetime import datetime, timezone
import json
import os
from uuid import uuid4

import storage

CAP = Decimal('2.00')
_scope = ContextVar('development_completion_scope', default=None)


class BudgetError(ValueError):
    pass


@contextmanager
def development_mode(*, prices=None, historical=False):
    """Prices must be verified upper bounds for all text-completion charges.

    prices: {model: {input_per_token, output_per_token, per_request,
                    valid_until: UTC ISO timestamp, all_charges_known: True}}
    This scope is NOT authorization for historical initialization.
    """
    token = _scope.set({'purpose': 'historical_backfill' if historical else 'development',
                        'prices': prices})
    try:
        yield
    finally:
        _scope.reset(token)


def _money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise BudgetError('Missing or invalid budget/price.') from None


@contextmanager
def _ledger_lock(root):
    # Exclusive creation also fails closed after a crash leaves a lock behind.
    root.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(root / 'reservation.lock', os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise BudgetError('Development ledger is locked; explicit recovery required.') from None
    try:
        os.close(fd)
        yield
    finally:
        (root / 'reservation.lock').unlink()


def initialize(root=None):
    """Explicit first-use setup only. Never overwrite a ledger or old records."""
    root = root if root is not None else storage.DATA / 'dev_budget'
    with _ledger_lock(root):
        if any(p.name != 'reservation.lock' for p in root.iterdir()):
            raise BudgetError('Development ledger already exists or needs recovery.')
        storage.write_json(root / 'initialized.json', {'schema': 1, 'at': storage.now()})
        storage.write_json(root / 'ledger.json', {'schema': 1, 'cap_usd': str(CAP),
                                                  'reserved_usd': '0', 'entries': []})


def reserve_if_development(messages, model, max_tokens, *, purpose=None, root=None):
    scope = _scope.get()
    if purpose is None and scope is None:
        return None  # Existing end-user behavior.
    if purpose not in (None, 'development', 'historical_backfill'):
        raise BudgetError('Unknown completion purpose.')
    if scope is None:
        raise BudgetError('Explicit development test scope is required.')
    if os.environ.get('P1_DEV_LIVE_OPT_IN') != '1':
        raise BudgetError('Development live completions are disabled.')
    purpose = ('historical_backfill' if scope['purpose'] == 'historical_backfill'
               else purpose or scope['purpose'])
    if purpose == 'historical_backfill' and os.environ.get('P1_HISTORY_LIVE_AUTHORIZED') != '1':
        raise BudgetError('Live historical initialization requires separate authorization.')
    ceiling = _money(os.environ.get('P1_DEV_BUDGET_USD'))
    if not 0 < ceiling <= CAP:
        raise BudgetError('An explicit development ceiling of at most US$2.00 is required.')
    if type(max_tokens) is not int or max_tokens <= 0:
        raise BudgetError('Development requests require a bounded maximum output token count.')
    try:
        price = scope['prices'][model]
        if price['all_charges_known'] is not True:
            raise ValueError
        expiry = datetime.fromisoformat(price['valid_until'])
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError
        input_price, output_price, request_price = (
            _money(price[k]) for k in ('input_per_token', 'output_per_token', 'per_request'))
    except (KeyError, TypeError, ValueError):
        raise BudgetError('Unknown, incomplete or expired model prices; request refused.') from None
    if (not isinstance(messages, list) or not messages
            or any(not isinstance(m, dict) or set(m) != {'role', 'content'}
                   or m['role'] not in ('system', 'user', 'assistant')
                   or not isinstance(m['content'], str) for m in messages)):
        raise BudgetError('Only plain text chat messages have a supported cost bound.')
    # UTF-8 bytes are a conservative token bound; add ample message framing and
    # a 2x margin. Output bound is sent to the provider by engine.complete().
    input_bound = 2 * (len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))
                       + 256 * len(messages) + 1024)
    amount = ((input_bound * input_price + max_tokens * output_price + request_price)
              * Decimal('1.10')).quantize(Decimal('0.000000001'), rounding=ROUND_CEILING)
    root = root if root is not None else storage.DATA / 'dev_budget'
    with _ledger_lock(root):
        try:
            ledger = json.loads((root / 'ledger.json').read_text(encoding='utf-8'))
            if ledger['schema'] != 1 or _money(ledger['cap_usd']) != CAP:
                raise ValueError
            entries = ledger['entries']
            if not isinstance(entries, list):
                raise ValueError
            total = sum((_money(e['reserved_usd']) for e in entries), Decimal(0))
            if total != _money(ledger['reserved_usd']) or total > CAP:
                raise ValueError
        except (OSError, KeyError, TypeError, ValueError):
            raise BudgetError('Development ledger absent or corrupt; request refused.') from None
        if total + amount > min(ceiling, CAP):
            raise BudgetError('Development budget exhausted; request refused.')
        entry = {'id': uuid4().hex, 'purpose': purpose,
                 'reserved_usd': str(amount), 'at': storage.now()}
        ledger['entries'].append(entry)
        ledger['reserved_usd'] = str(total + amount)
        storage.write_json(root / 'ledger.json', ledger)
        return entry['id']
