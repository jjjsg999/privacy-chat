"""Real-model check of the web search planner (HANDOFF_WEB_SEARCH.md section 10). Paid: run only with approval.

    .\\.venv\\Scripts\\python.exe -m eval.run_search_planner_check            # dry run: shows what would be sent
    .\\.venv\\Scripts\\python.exe -m eval.run_search_planner_check --live     # 8 planner calls, capped at US$0.05
    ... --live --model provider/model --cap 0.05

Uses the saved chat model unless --model is given. Spend is capped through dev_budget.py (development scope, verified
prices from the live catalogue, a ceiling of what is already reserved there plus --cap), and still goes through the
monthly guard. The cases are invented and already masked (eval/search_planner_cases.json); nothing personal is sent.

Pass: at least 7 of 8 replies valid, no case over the query limit, no placeholder invented.
"""
import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import dev_budget
import engine
import storage
import web_search
from privacy_engine import TOKEN, parse_terms

HERE = Path(__file__).resolve().parent
CASES = HERE / 'search_planner_cases.json'
LIMIT = 3


def planner_request(conversation, limit=LIMIT):
    prompt = engine.planner_prompt(limit)
    return [{'role': 'system', 'content': prompt}] + [{'role': m['role'], 'content': m['content']} for m in conversation]


def evaluate(case, reply, terms, seconds):
    """One report row: valid JSON, number of queries, placeholders a subset of the input's, labels, time."""
    given = {t for m in case['conversation'] for t in TOKEN.findall(m['content'])}
    row = {'case': case['id'], 'seconds': round(seconds, 1), 'valid': False, 'queries': 0, 'over_limit': False,
           'invented': [], 'labels': [], 'expect_search': case['expect_search'], 'error': None}
    try:
        plan = engine.parse_plan(reply, 99)
    except ValueError as exc:
        row['error'] = str(exc)
        return row
    row.update(valid=True, queries=len(plan), over_limit=len(plan) > LIMIT)
    row['missed_explicit'] = bool(case.get('explicit_search')) and not plan  # an explicit request must get a query
    this_year = datetime.now().year  # years older than this in a query usually mean the model guessed the date
    row['stale_years'] = sorted({y for item in plan for y in re.findall(r'\b(?:19|20)\d\d\b', item['query']) if int(y) < this_year})
    for item in plan:
        row['invented'] += sorted(set(TOKEN.findall(item['query'])) - given)
        try:
            # As in the app with dates passing (the default): a date written out by the planner is fine.
            checked = web_search.check_query(item['query'], dict(case['token_map']), terms,
                                             allowed=lambda token, value: token.startswith('DATE_TIME__'))
            row['labels'].append(checked['kind'])
        except web_search.CheckError:
            row['labels'].append('failed check')
    row['planned'] = [item['query'] for item in plan]
    return row


def dev_ceiling(cap):
    """The dev ledger ceiling that allows `cap` more than what is already reserved there."""
    ledger = json.loads((storage.DATA / 'dev_budget' / 'ledger.json').read_text(encoding='utf-8'))
    ceiling = Decimal(ledger['reserved_usd']) + Decimal(str(cap))
    if ceiling > dev_budget.CAP:
        raise SystemExit(f'The development ledger has too little room left under its US${dev_budget.CAP} cap.')
    return ceiling


async def live(model, cap):
    catalogue = await engine.model_catalogue()
    pricing = next(m['pricing'] for m in catalogue if m['id'] == model)
    valid_until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    prices = {model: {'input_per_token': pricing.get('prompt', '0'), 'output_per_token': pricing.get('completion', '0'),
                      'per_request': pricing.get('request', '0'), 'valid_until': valid_until, 'all_charges_known': True}}
    if not (storage.DATA / 'dev_budget' / 'ledger.json').exists():
        dev_budget.initialize()
    os.environ['P1_DEV_LIVE_OPT_IN'] = '1'
    os.environ['P1_DEV_BUDGET_USD'] = str(dev_ceiling(cap))
    data = json.loads(CASES.read_text(encoding='utf-8'))
    terms = parse_terms(data['terms'])
    rows = []
    with dev_budget.development_mode(prices=prices):
        for case in data['cases']:
            started = time.monotonic()
            try:
                reply = await engine.complete(planner_request(case['conversation']), model, max_tokens=engine.PLANNER_MAX_TOKENS,
                                              purpose='development')
            except engine.ChatError as exc:
                rows.append({'case': case['id'], 'seconds': round(time.monotonic() - started, 1), 'valid': False, 'queries': 0,
                             'over_limit': False, 'invented': [], 'labels': [], 'expect_search': case['expect_search'],
                             'error': engine.failure_reason(exc)})
                continue
            rows.append(evaluate(case, reply, terms, time.monotonic() - started))
    return rows


def report(rows, model):
    print(f'Model: {model}')
    for row in rows:
        print(f"- {row['case']}: {'valid' if row['valid'] else 'INVALID'} · {row['queries']} queries"
              f"{' (OVER LIMIT)' if row['over_limit'] else ''} · invented placeholders: {row['invented'] or 'none'}"
              f" · labels: {', '.join(row['labels']) or '-'} · {row['seconds']} s"
              f"{' · stale years: ' + ', '.join(row['stale_years']) if row.get('stale_years') else ''}"
              f"{' · error: ' + row['error'] if row['error'] else ''}")
        for query in row.get('planned', []):
            print(f'    planner wrote: {query}')
    valid = sum(r['valid'] for r in rows)
    missed = [r['case'] for r in rows if r.get('missed_explicit')]
    passed = (valid >= len(rows) - 1 and not any(r['over_limit'] for r in rows) and not any(r['invented'] for r in rows)
              and not missed)
    print(f'Valid {valid}/{len(rows)} · over limit {sum(r["over_limit"] for r in rows)} · '
          f'invented {sum(bool(r["invented"]) for r in rows)} · explicit requests without a query: {missed or "none"}'
          f' -> {"PASS" if passed else "FAIL"}')
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', action='store_true', help='make the real (paid) planner calls')
    parser.add_argument('--model', help='model id; default: the saved chat model')
    parser.add_argument('--cap', type=float, default=0.05, help='spend cap in US$ (at most 0.05)')
    args = parser.parse_args()
    if not 0 < args.cap <= 0.05:
        raise SystemExit('The cap must be more than 0 and at most 0.05.')
    model = args.model or storage.settings()['model']
    if not args.live:
        data = json.loads(CASES.read_text(encoding='utf-8'))
        print(f'Dry run. {len(data["cases"])} cases would be sent to {model}, capped at US${args.cap}. Add --live to run.')
        return
    raise SystemExit(0 if report(asyncio.run(live(model, args.cap)), model) else 1)


if __name__ == '__main__':
    main()
