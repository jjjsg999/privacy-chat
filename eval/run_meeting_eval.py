"""Meeting extraction test: about 10 invented, noisy, Telegram-style transcripts with planted items, run through the
real analysis path (engine.analyze_meeting) with the configured meeting model.

  .\\.venv\\Scripts\\python.exe -m eval.run_meeting_eval            (costs money; default cap US$0.20)
  .\\.venv\\Scripts\\python.exe -m eval.run_meeting_eval --max-usd 0.10 --count 5

Every planted decision, action (owner, due date), risk, key figure and open question carries a unique invented keyword,
so matching is deterministic. Reports recall and precision per section, how many items were flagged unverified, and cost.

Safety: configuration comes from the app's own loading (keys are never read here or printed); calls go through the normal
monthly-budget reservation; --max-usd stops the run before a call whose worst-case cost would take the run past the cap.
Sessions are not saved (storage.save_session is replaced by a no-op), so nothing is written to data/sessions.
The unit tests run this module only against a fake model.
"""
import argparse
import asyncio
import json
import random
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import engine
import monthly_budget
import storage
from privacy_engine import PrivacyEngine

HERE = Path(__file__).resolve().parent
SECTIONS = ('decisions', 'actions', 'open_questions', 'risks', 'key_figures')
SYLLABLES = ['bran', 'tol', 'vex', 'quar', 'mil', 'dro', 'zan', 'pel', 'fio', 'hux', 'lar', 'nev', 'sor', 'tum', 'wel', 'yar']
NAMES = ['Imogen Hartley', 'Tariq Bessant', 'Noor Alvarez', 'Piotr Lindgren', 'Wen-Ting Chao', 'Ife Adeyemi', 'Greta Moll',
         'Rafael Quist', 'Marisol Dunne', 'Kofi Brandt', 'Sunniva Reyes', 'Ludo Pereira']
DUES = ['next Friday', '5 Dec 2026', 'end of the month', 'Monday', '2026-12-15', 'before the board meeting', 'Dec 20th', 'tomorrow']
FILLER = ['um', 'yeah', 'ok', 'lol', 'right right', '[inaudible]', 'sorry cut out', 'can you hear me?', 'hmm', '...', 'wait what',
          'agreed', 'one sec', 'ok ok', 'k', 'noted', 'sorry go on', 'anyway', 'as I said', 'so yeah']
SPEAKERS = ['Imo', 'Tariq', 'Noor', 'Piotr', 'WT', 'Ife', 'Greta', 'Raf']


class StopEval(engine.ChatError):
    """The --max-usd cap would be exceeded by the next call."""


# ------------------------------------------------------------------ generator

def unique_word(rng, used):
    while True:
        word = ''.join(rng.choice(SYLLABLES) for _ in range(3)).capitalize()
        if word.lower() not in used:
            used.add(word.lower())
            return word


def generate(count=10, seed=20261123):
    rng, used, meetings = random.Random(seed), set(), []
    for number in range(count):
        planted = {s: [] for s in SECTIONS}
        sentences = []
        for _ in range(rng.randint(1, 2)):
            kw = unique_word(rng, used)
            planted['decisions'].append({'keyword': kw})
            sentences.append(f"ok so we're going with the {kw} option, that's decided.")
        for _ in range(rng.randint(1, 2)):
            kw, owner, due = unique_word(rng, used), rng.choice(NAMES), rng.choice(DUES)
            planted['actions'].append({'keyword': kw, 'owner': owner, 'due': due})
            sentences.append(f'{owner} will handle the {kw} filing by {due}.')
        kw = unique_word(rng, used)
        planted['risks'].append({'keyword': kw})
        sentences.append(f'biggest risk is the {kw} supplier slipping.')
        kw, figure = unique_word(rng, used), f'${rng.randint(2, 98)},{rng.randint(100, 999)},000'
        planted['key_figures'].append({'keyword': kw, 'figure': figure})
        sentences.append(f'the {kw} budget is {figure} total.')
        kw = unique_word(rng, used)
        planted['open_questions'].append({'keyword': kw})
        sentences.append(f'still unclear who signs off on {kw}?')
        rng.shuffle(sentences)
        lines = []
        for sentence in sentences:
            for _ in range(rng.randint(2, 6)):  # fragments and filler between the planted statements
                lines.append(('' if rng.random() < .35 else rng.choice(SPEAKERS) + ': ') + rng.choice(FILLER))
            lines.append(('' if rng.random() < .3 else rng.choice(SPEAKERS) + ': ') + sentence)
            if rng.random() < .15:
                lines.append(lines[-1])  # a batching duplicate
        lines.insert(rng.randrange(len(lines)), 'Raf: <b>note</b> see <script>alert(1)</script> ok')  # HTML-like text must stay inert
        meetings.append({'id': f'm{number + 1:02d}', 'text': '\n'.join(lines), 'planted': planted})
    return meetings


# ------------------------------------------------------------------ scoring

def item_text(section, item):
    if section == 'actions':
        return item['task']
    if section == 'key_figures':
        return f"{item['figure']} {item.get('context', '')}"
    return item['text']


def score_meeting(record, planted):
    """Per section: planted items found, output items that match a planted keyword, and action details."""
    out = {}
    for section in SECTIONS:
        items = record.get(section, []) if isinstance(record, dict) else []
        found = 0
        matched_items = set()
        owner_ok = due_ok = 0
        for plant in planted[section]:
            hit = next((i for i, item in enumerate(items) if plant['keyword'].casefold() in item_text(section, item).casefold()), None)
            if hit is None:
                continue
            found += 1
            matched_items.add(hit)
            if section == 'actions':
                item = items[hit]
                owner_ok += bool(item.get('owner')) and plant['owner'].split()[-1].casefold() in item['owner'].casefold()
                due_ok += bool(item.get('due')) and plant['due'].casefold() in item['due'].casefold()
            if section == 'key_figures':
                owner_ok += re.sub(r'\D', '', plant['figure']) in re.sub(r'\D', '', items[hit]['figure'])
        out[section] = {'planted': len(planted[section]), 'found': found, 'output': len(items), 'matching': len(matched_items),
                        'unverified': sum(1 for i in items if not i.get('verified', True)),
                        'unverified_planted': sum(1 for i in matched_items if not items[i].get('verified', True)),
                        'owner_ok': owner_ok if section == 'actions' else None, 'due_ok': due_ok if section == 'actions' else None,
                        'figure_ok': owner_ok if section == 'key_figures' else None}
    return out


def combine(scores):
    summary = {}
    for section in SECTIONS:
        rows = [s[section] for s in scores]
        planted, found = sum(r['planted'] for r in rows), sum(r['found'] for r in rows)
        output, matching = sum(r['output'] for r in rows), sum(r['matching'] for r in rows)
        summary[section] = {'planted': planted, 'found': found, 'recall_pct': round(100 * found / planted, 1) if planted else None,
                            'output': output, 'matching': matching, 'precision_pct': round(100 * matching / output, 1) if output else None,
                            'unverified': sum(r['unverified'] for r in rows), 'unverified_planted': sum(r['unverified_planted'] for r in rows)}
        if section == 'actions':
            summary[section].update(owner_correct=sum(r['owner_ok'] for r in rows), due_correct=sum(r['due_ok'] for r in rows))
        if section == 'key_figures':
            summary[section]['figure_correct'] = sum(r['figure_ok'] for r in rows)
    return summary


# ------------------------------------------------------------------ the run

class SpendGuard:
    """Refuses a call whose worst-case cost would take this run past max_usd."""

    def __init__(self, max_usd, spent, worst):
        self.max_usd, self.spent, self.worst, self.tripped = Decimal(str(max_usd)), spent, worst, False

    def check(self, messages, max_tokens):
        if self.spent() + self.worst(messages, max_tokens) > self.max_usd:
            self.tripped = True
            raise StopEval(f'Stopped before a call that could take this run past US${self.max_usd}.')


async def run(meetings, privacy, complete, guard=None):
    """Run engine.analyze_meeting on each meeting with `complete` as the provider call. Returns the results."""
    results = []

    async def guarded(messages, model, max_tokens=None, **kwargs):
        if guard:
            guard.check(messages, 6000 if max_tokens is None else max_tokens)
        return await complete(messages, model, max_tokens, **kwargs) if max_tokens is not None else await complete(messages, model, **kwargs)

    with patch.object(engine, 'complete', guarded), patch.object(storage, 'save_session', lambda session: None):
        for meeting in meetings:
            if guard and guard.tripped:
                break
            session = storage.new_session('telegram')
            session['session_type'] = 'meeting'
            try:
                public = await engine.analyze_meeting(session, meeting['text'], privacy)
            except engine.ChatError as exc:
                results.append({'meeting': meeting['id'], 'error': str(exc)})
                continue
            record = public['intelligence']
            results.append({'meeting': meeting['id'], 'structured': record.get('structured', False), 'failure': record.get('failure'),
                            'scores': score_meeting(record, meeting['planted'])})
    return results


def summarize(results):
    done = [r for r in results if 'scores' in r]
    return {'meetings_run': len(done), 'meetings_failed': len(results) - len(done),
            'structured_ok': sum(1 for r in done if r['structured']), 'sections': combine([r['scores'] for r in done]) if done else {}}


def markdown(report):
    s = report['summary']
    lines = [f"# Meeting extraction test ({report['date']})", '',
             f"Model: `{report['model']}` · meetings run: {s['meetings_run']} of {report['meetings']} · structured record valid: {s['structured_ok']} · "
             f"cost: US${report['cost_usd']} (cap US${report['max_usd']})" + (' · **stopped at the cap**' if report['stopped'] else ''), '',
             '| Section | Planted | Recall % | Output items | Precision % | Unverified (of output) | Unverified planted items |', '|---|---|---|---|---|---|---|']
    for section in SECTIONS:
        r = s['sections'].get(section)
        if r:
            lines.append(f"| {section} | {r['planted']} | {r['recall_pct']} | {r['output']} | {r['precision_pct']} | {r['unverified']} | {r['unverified_planted']} |")
    actions, figures = s['sections'].get('actions', {}), s['sections'].get('key_figures', {})
    if actions:
        lines += ['', f"Actions found with the right owner: {actions['owner_correct']} / {actions['found']}; with the due date as said: {actions['due_correct']} / {actions['found']}."]
    if figures:
        lines += [f"Key figures with the right digits: {figures['figure_correct']} / {figures['found']}."]
    lines += ['', 'Precision counts output items that match a planted keyword, so a correct but unplanted item lowers it.']
    failed = [r for r in report['results'] if 'error' in r or not r.get('structured', True)]
    if failed:
        lines += ['', 'Meetings with problems:'] + [f"- {r['meeting']}: {r.get('error') or r.get('failure')}" for r in failed]
    return '\n'.join(lines) + '\n'


async def real_run(count, max_usd, seed):
    """The configured model, through the normal budget reservation, capped at max_usd."""
    config = storage.settings()
    model = engine.meeting_model(config)
    catalogue = await engine.model_catalogue()
    rates = monthly_budget.prices(catalogue, model)  # raises if the price is unknown: nothing is sent
    start = Decimal(monthly_budget.status().get('used_usd', '0'))

    def spent():
        return Decimal(monthly_budget.status().get('used_usd', '0')) - start

    def worst(messages, max_tokens):  # the same bound monthly_budget.reserve() uses
        bound = 2 * (len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 256 * len(messages) + 1024)
        return (bound * rates[0] + max_tokens * rates[1] + rates[2]) * Decimal('1.10')

    guard = SpendGuard(max_usd, spent, worst)
    results = await run(generate(count, seed), PrivacyEngine(), engine.complete, guard)
    return model, results, spent(), guard.tripped


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--max-usd', type=Decimal, default=Decimal('0.20'))
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--seed', type=int, default=20261123)
    args = parser.parse_args()
    model, results, cost, stopped = asyncio.run(real_run(args.count, args.max_usd, args.seed))
    report = {'date': date.today().isoformat(), 'model': model, 'meetings': args.count, 'max_usd': str(args.max_usd),
              'cost_usd': str(cost.quantize(Decimal('0.0001'))), 'stopped': stopped, 'summary': summarize(results), 'results': results}
    stem = HERE / 'results' / f"{report['date']}_meeting_eval"
    stem.parent.mkdir(exist_ok=True)
    stem.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    stem.with_suffix('.md').write_text(markdown(report), encoding='utf-8')
    print(markdown(report))


if __name__ == '__main__':
    main()
