"""Local, durable calendar-month spending guard shared by every completion."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import json
from uuid import uuid4

from dev_budget import BudgetError, _money, _ledger_lock
import storage

SG = timezone(timedelta(hours=8))


def period():
    return datetime.now(SG).strftime('%Y-%m')


def ceiling():
    return _money(storage.settings().get('monthly_cost_ceiling_usd', '2.00'))


def _root():
    return storage.DATA / 'monthly_budget'


def _read():
    root = _root(); path = root / 'ledger.json'; marker = root / 'initialized.json'
    if not path.exists() and not marker.exists():
        if root.exists() and any(p.name != 'reservation.lock' for p in root.iterdir()):
            raise BudgetError('Monthly cost ledger is incomplete; requests are blocked.')
        return {'schema': 1, 'started_at': None, 'entries': [], 'blocked': False}
    try:
        if not marker.exists():
            raise ValueError()
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['schema'] != 1 or not isinstance(data['entries'], list) or type(data['blocked']) is not bool or not data['started_at']:
            raise ValueError()
        ids = set()
        for entry in data['entries']:
            if entry['id'] in ids or len(entry['month']) != 7 or entry['state'] not in ('reserved', 'settled'):
                raise ValueError()
            ids.add(entry['id']); _money(entry['reserved_usd']); _money(entry['charged_usd'])
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise BudgetError('Monthly cost ledger is unavailable or corrupt; requests are blocked.') from None


def _used(data, month):
    return sum((_money(e['charged_usd']) for e in data['entries'] if e['month'] == month), Decimal(0))


def status():
    limit = ceiling(); month = period()
    try:
        data = _read(); used = _used(data, month)
        pending = sum((_money(e['charged_usd']) for e in data['entries']
                       if e['month'] == month and e['state'] == 'reserved'), Decimal(0))
        return {'month': month, 'timezone': 'Asia/Singapore', 'ceiling_usd': str(limit),
                'used_usd': str(used), 'reserved_usd': str(pending),
                'remaining_usd': str(max(Decimal(0), limit-used)), 'started_at': data['started_at'],
                'available': not data['blocked'],
                'error': 'Provider charge exceeded its reservation; requests blocked pending reconciliation.' if data['blocked'] else None}
    except (BudgetError, OSError):
        return {'month': month, 'timezone': 'Asia/Singapore', 'ceiling_usd': str(limit),
                'available': False, 'error': 'Monthly ledger unavailable; requests blocked. Existing files retained.'}


def spend_by_model():
    """This month's charged amounts grouped by model. Unsettled reservations count at their reserved amount."""
    month = period()
    try:
        data = _read()
    except (BudgetError, OSError):
        return {'month': month, 'available': False, 'models': [], 'untracked_usd': '0', 'total_usd': '0'}
    totals, untracked, total = {}, Decimal(0), Decimal(0)
    for entry in data['entries']:
        if entry['month'] != month:
            continue
        amount = _money(entry['charged_usd']); total += amount
        model = entry.get('model')
        if not isinstance(model, str) or not model:
            untracked += amount  # recorded before per-model tracking existed
            continue
        row = totals.setdefault(model, {'model': model, 'usd': Decimal(0), 'calls': 0, 'unsettled': 0})
        row['usd'] += amount; row['calls'] += 1; row['unsettled'] += entry['state'] == 'reserved'
    models = sorted(({**row, 'usd': str(row['usd'])} for row in totals.values()), key=lambda r: Decimal(r['usd']), reverse=True)
    return {'month': month, 'available': True, 'models': models, 'untracked_usd': str(untracked), 'total_usd': str(total)}


def prices(catalogue, model):
    try:
        if ':online' in model:
            raise ValueError()
        price = next(m['pricing'] for m in catalogue if m['id'] == model)
        known = {'prompt', 'completion', 'request', 'web_search', 'input_cache_read', 'input_cache_write', 'input_cache_write_1h'}
        if set(price) - known:
            raise ValueError()
        amounts = {k: _money(v) for k, v in price.items()}
        # Text-only requests have no search/plugin tools. Cache rates replace
        # ordinary input rates; reserve the highest, never assume a discount.
        prompt = max([amounts['prompt']] + [v for k, v in amounts.items() if k.startswith('input_cache')])
        return prompt, amounts['completion'], amounts.get('request', Decimal(0))
    except (KeyError, ValueError, TypeError, StopIteration):
        raise BudgetError('Model pricing is unknown or unsupported; no paid request sent.') from None


def reserve(messages, max_tokens, rates, model=None):
    if type(max_tokens) is not int or max_tokens <= 0 or not messages or any(
            set(m) != {'role', 'content'} or not isinstance(m['content'], str) for m in messages):
        raise BudgetError('Monthly guard requires bounded plain-text requests.')
    prompt, output, request = rates
    bound = 2 * (len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 256 * len(messages) + 1024)
    amount = ((bound * prompt + max_tokens * output + request) * Decimal('1.10')).quantize(Decimal('.000000001'), rounding=ROUND_CEILING)
    with _ledger_lock(_root()):
        data = _read(); month = period(); remaining = max(Decimal(0), ceiling() - _used(data, month))
        if data['blocked']:
            raise BudgetError('Monthly ledger needs reconciliation; paid requests are blocked.')
        if ceiling() == 0 or amount > remaining:
            raise BudgetError(f'Monthly cost ceiling: request needs up to US${amount:.4f}, remaining US${remaining:.4f}. Adjust the ceiling in Settings or wait until next month (Singapore time).')
        if data['started_at'] is None:
            data['started_at'] = storage.now()
            storage.write_json(_root() / 'initialized.json', {'schema': 1, 'at': data['started_at']})
        identifier = uuid4().hex
        entry = {'id': identifier, 'month': month, 'at': storage.now(),
                 'reserved_usd': str(amount), 'charged_usd': str(amount), 'state': 'reserved'}
        if model:  # lets the Models page show spend per model; older entries simply have none
            entry['model'] = str(model)[:200]
        data['entries'].append(entry)
        storage.write_json(_root() / 'ledger.json', data)
    return identifier


def settle(identifier, usage):
    # Missing/ambiguous costs and failed calls retain the full reservation.
    if not isinstance(usage, dict) or usage.get('cost') is None:
        return
    try:
        cost = _money(usage['cost'])
        # Include direct-provider (BYOK) cost if OpenRouter reports it separately.
        details = usage.get('cost_details') or {}
        if usage.get('is_byok') or details.get('upstream_inference_cost'):
            cost += _money(details.get('upstream_inference_cost', 0))
    except (BudgetError, TypeError, AttributeError):
        return
    with _ledger_lock(_root()):
        data = _read()
        entry = next((e for e in data['entries'] if e['id'] == identifier), None)
        if entry is None:
            raise BudgetError('Monthly reservation missing; requests blocked.')
        if entry['state'] == 'settled':
            return
        if cost > _money(entry['reserved_usd']):
            data['blocked'] = True
        entry.update(charged_usd=str(cost), state='settled')
        storage.write_json(_root() / 'ledger.json', data)


def provider_limits(rates):
    return {'max_price': {'prompt': float(rates[0] * 1_000_000),
                          'completion': float(rates[1] * 1_000_000), 'request': float(rates[2])}}
