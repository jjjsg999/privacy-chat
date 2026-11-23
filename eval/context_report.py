"""Estimated tokens sent per turn: today (whole history every turn) versus Lean, Balanced and Full.

  .\\.venv\\Scripts\\python.exe -m eval.context_report

Invented chats only, no model calls, no data/ access. Also times masking a 50,000-character invented
transcript on a first pass and on a cached pass (the speed check). Writes eval/results/<date>_context_report.md/.json.
"""
import json
import random
import tempfile
import time
from datetime import date
from pathlib import Path
from unittest.mock import patch

import context_builder as cb
import engine
import storage
from eval import generate_testset
from privacy_engine import PrivacyEngine

HERE = Path(__file__).resolve().parent
CONTEXT_LENGTH = 128_000   # a typical large-context model; Full is bounded by this
OUT_RESERVE = 16_000       # the streamed reply limit the app reserves
SYSTEM = 'You are a helpful personal assistant. ' * 30 + 'x' * 6_000  # base prompt + artifacts prompt, roughly
SEED = 20261123

WORDS = ('budget plan quarter forecast roadmap hiring vendor contract timeline migration review risk owner '
         'milestone launch pricing support onboarding report metrics customer').split()


def prose(rng, chars):
    out = []
    while sum(map(len, out)) + len(out) < chars:
        out.append(rng.choice(WORDS))
    return ' '.join(out)


def artifact(artifact_id, title, body):
    return f'Here is the latest version.\n<artifact type="html" title="{title}" id="{artifact_id}">\n{body}\n</artifact>\nTell me what to change.'


def scenario_long_chat(rng):
    turns = [(prose(rng, 300), prose(rng, 4_000)) for _ in range(40)]
    return 'A 40-turn chat', [(q, a, None, None) for q, a in turns], {}


def scenario_upload(rng):
    block = f'[Attached file: report.pdf]\n{prose(rng, 60_000)}\n[End of attached file]'
    turns = [(f'Summarise this file.\n\n{block}', prose(rng, 3_000), [{'name': 'report.pdf', 'chars': 60_000}], None)]
    turns += [(prose(rng, 250), prose(rng, 2_500), None, None) for _ in range(14)]
    return 'A chat with a 60,000-character upload', turns, {}


def scenario_artifact(rng):
    turns = []
    for version in range(1, 7):
        body = prose(rng, 12_000)
        turns.append((f'Please {"create" if version == 1 else "change"} the page (round {version}).',
                      artifact('landing', 'Landing page', body), None, None))
    turns += [(prose(rng, 200), prose(rng, 1_500), None, None) for _ in range(6)]
    return 'A chat with one artifact edited 6 times', turns, {}


def scenario_meeting(rng):
    transcript = prose(rng, 80_000)
    turns = [(prose(rng, 120) + '?', prose(rng, 1_500), None, None) for _ in range(25)]
    return 'A meeting with 25 questions and answers', turns, {'meeting': transcript}


def items_for(turns, upto, extra):
    """Items as engine.chat_context / meeting_context would build them for turn `upto` (0-based)."""
    items = []
    if extra.get('meeting'):
        items += [engine.transcript_item(extra['meeting']), {'role': 'assistant', 'text': prose(random.Random(1), 3_000), 'whole': True,
                                                             'internal': True, 'parse': False}]
    index = 0
    for question, answer, attachments, _ in turns[:upto]:
        items.append({'role': 'user', 'text': question, 'index': index, 'attachments': attachments})
        items.append({'role': 'assistant', 'text': answer, 'real': answer, 'index': index + 1})
        index += 2
    items.append({'role': 'user', 'text': turns[upto][0], 'index': None, 'attachments': turns[upto][2]})
    return items


def sent_tokens(items, cap):
    built = cb.build_context(SYSTEM, items, cap=cap, context_length=CONTEXT_LENGTH, out_reserve=OUT_RESERVE)
    return built['info']['est_tokens']


def today_tokens(items):
    return cb.message_tokens(SYSTEM) + sum(cb.message_tokens(i['text']) for i in items)


def run_scenarios():
    rng = random.Random(SEED)
    results = []
    for build in (scenario_long_chat, scenario_upload, scenario_artifact, scenario_meeting):
        name, turns, extra = build(rng)
        meeting = bool(extra.get('meeting'))
        rows = []
        for upto in range(len(turns)):
            items = items_for(turns, upto, extra)
            rows.append({'turn': upto + 1, 'today': today_tokens(items),
                         **{mode: sent_tokens(items, cb.CAPS[mode]) for mode in cb.MODES}})
        total = {key: sum(r[key] for r in rows) for key in ('today', *cb.MODES)}
        results.append({'scenario': name, 'meeting': meeting, 'turns': rows, 'totals': total})
    return results


def speed_check():
    """Masking a 50,000-character invented transcript: first pass versus cached pass (best of 3 for the cached one)."""
    snippets = [json.loads(line)['text'] for line in generate_testset.TESTSET.read_text(encoding='utf-8').splitlines() if line.strip()]
    text, i = '', 0
    while len(text) < 50_000:
        text += snippets[i % len(snippets)] + '\n'
        i += 1
    text = text[:50_000]
    terms_text = generate_testset.EVAL_TERMS.read_text(encoding='utf-8')
    from privacy_engine import parse_terms
    terms = parse_terms(terms_text)
    privacy = PrivacyEngine()
    out = {'characters': len(text)}
    for mode in ('standard', 'layered'):
        privacy.mode = mode
        holder, token_map = {}, {}
        started = time.perf_counter()
        fingerprint = privacy.fingerprint(terms_text)
        engine.cached_mask(holder, 'masked', text, privacy, token_map, terms, fingerprint)
        first = time.perf_counter() - started
        best = None
        for _ in range(3):
            started = time.perf_counter()
            fingerprint = privacy.fingerprint(terms_text)
            engine.cached_mask(holder, 'masked', text, privacy, token_map, terms, fingerprint)
            best = min(best or 9e9, time.perf_counter() - started)
        out[mode] = {'first_pass_s': round(first, 3), 'cached_pass_s': round(best, 5)}
    return out


def markdown(results, speed):
    lines = [f'# Context report ({date.today().isoformat()})', '',
             f'Estimated tokens sent per turn, including a {cb.estimate_tokens(SYSTEM):,}-token system prompt. Model context {CONTEXT_LENGTH:,}, '
             f'reply reserve {OUT_RESERVE:,}. Caps: lean {cb.CAPS["lean"]:,}, balanced {cb.CAPS["balanced"]:,}, full = model limit. '
             '"Today" is the old behaviour: the whole history every turn. Invented chats; no model calls.', '']
    for r in results:
        t = r['totals']
        lines += [f"## {r['scenario']}", '', '| Turn | Today | Lean | Balanced | Full |', '|---|---|---|---|---|']
        picks = sorted({1, 2, 5, 10, 15, 20, 25, 30, 40, len(r['turns'])} & set(range(1, len(r['turns']) + 1)))
        for row in r['turns']:
            if row['turn'] in picks:
                lines.append(f"| {row['turn']} | {row['today']:,} | {row['lean']:,} | {row['balanced']:,} | {row['full']:,} |")
        saved = {m: round(100 * (1 - t[m] / t['today'])) for m in cb.MODES}
        lines += [f"| **Whole chat** | **{t['today']:,}** | **{t['lean']:,}** ({saved['lean']}% less) | **{t['balanced']:,}** ({saved['balanced']}% less) | **{t['full']:,}** ({saved['full']}% less) |", '']
    lines += ['## Masking speed (50,000-character invented transcript)', '', '| Detector | First pass | Cached pass |', '|---|---|---|']
    for mode in ('standard', 'layered'):
        s = speed[mode]
        lines.append(f"| {mode} | {s['first_pass_s']} s | {s['cached_pass_s'] * 1000:.2f} ms |")
    return '\n'.join(lines) + '\n'


def main():
    results = run_scenarios()
    with tempfile.TemporaryDirectory() as temp, patch.object(storage, 'DATA', Path(temp) / 'data'):
        speed = speed_check()
    stem = HERE / 'results' / f'{date.today().isoformat()}_context_report'
    stem.parent.mkdir(exist_ok=True)
    stem.with_suffix('.json').write_text(json.dumps({'scenarios': results, 'speed': speed}, indent=2), encoding='utf-8')
    stem.with_suffix('.md').write_text(markdown(results, speed), encoding='utf-8')
    print(markdown(results, speed))


if __name__ == '__main__':
    main()
