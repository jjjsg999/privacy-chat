"""Precision/recall comparison: Setup A ("standard") vs Setup B ("layered"). Offline, no model API calls.

  .\\.venv\\Scripts\\python.exe -m eval.run_privacy_eval

Everything scored is invented (eval/testset.jsonl, eval/eval_terms.txt). The real data/, .env,
config.json and sensitive_terms.txt are never read. Both setups run through the real
PrivacyEngine.tokenize(); spans are recovered from its output, so the scored code path is the shipped one.
"""
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

from eval import generate_testset
from privacy_engine import PrivacyEngine, parse_terms

# Adoption rule, fixed before any run: adopt Setup B if recall rises by at least this many
# percentage points AND precision falls by no more than this many points.
ADOPT_MIN_RECALL_GAIN_POINTS = 2.0
ADOPT_MAX_PRECISION_DROP_POINTS = 5.0

HERE = Path(__file__).resolve().parent
# Looser than privacy_engine.TOKEN on purpose: a token glued to CJK text (PERSON__3跟) must still align.
TOKEN_ANYWHERE = re.compile(r'[A-Z][A-Z0-9_]*__\d+')
RESULTS = HERE / 'results'
SETUPS = {'A': 'standard', 'B': 'layered'}


def load_testset():
    if not generate_testset.TESTSET.exists():
        generate_testset.main()
    return [json.loads(line) for line in generate_testset.TESTSET.read_text(encoding='utf-8').splitlines() if line.strip()]


def masked_spans(privacy, snippet, terms):
    """Run the real tokenize() and recover (start, end, kind) spans over the original text."""
    token_map = {}
    masked = privacy.tokenize(snippet['text'], token_map, terms)
    spans, cursor, position = [], 0, 0
    for match in TOKEN_ANYWHERE.finditer(masked):
        position += match.start() - cursor
        cursor = match.end()
        value = token_map[match[0]]
        if snippet['text'][position:position + len(value)] != value:
            raise RuntimeError(f"{snippet['id']}: cannot align {match[0]} with the original text")
        spans.append((position, position + len(value), match[0].rsplit('__', 1)[0]))
        position += len(value)
    return masked, spans


def merged_cover(spans):
    merged = []
    for start, end, _ in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def evaluate(privacy, snippets, terms):
    stats = {'must': 0, 'covered': 0, 'spans_counted': 0, 'spans_true': 0, 'excluded_neutral': 0, 'must_pass_masked': 0}
    by_category, missed, false_positives, sanity = {}, [], [], []
    started = time.perf_counter()
    for snippet in snippets:
        masked, spans = masked_spans(privacy, snippet, terms)
        cover = merged_cover(spans)
        uncovered_same, covered_values = {}, set()
        for item in snippet['must_mask']:
            stats['must'] += 1
            category = by_category.setdefault(item['category'], {'must': 0, 'covered': 0})
            category['must'] += 1
            if any(s <= item['start'] and item['end'] <= e for s, e in cover):
                stats['covered'] += 1
                category['covered'] += 1
                covered_values.add(item['value'])
            else:
                uncovered_same[item['value']] = uncovered_same.get(item['value'], 0) + 1
                missed.append({'snippet': snippet['id'], 'value': item['value'], 'category': item['category']})
        for start, end, kind in spans:
            if any(start < m['end'] and end > m['start'] for m in snippet['must_mask']):
                stats['spans_counted'] += 1
                stats['spans_true'] += 1
            elif any(n['start'] <= start and end <= n['end'] for n in snippet['neutral']):
                stats['excluded_neutral'] += 1
            else:
                stats['spans_counted'] += 1
                value = snippet['text'][start:end]
                false_positives.append({'snippet': snippet['id'], 'value': value, 'kind': kind,
                                        'must_pass_word': any(p['start'] < end and p['end'] > start for p in snippet['must_pass'])})
        stats['must_pass_masked'] += sum(1 for p in snippet['must_pass'] if any(s < p['end'] and e > p['start'] for s, e, _ in spans))
        # Sanity: a value counted as covered must not survive verbatim anywhere it was not masked.
        for value in covered_values:
            left = generate_testset.locate(masked, value)
            remaining = [1 for s, e in generate_testset.locate(snippet['text'], value)
                         if not any(cs <= s and e <= ce for cs, ce in cover)]
            if len(left) > len(remaining):
                sanity.append({'snippet': snippet['id'], 'value': value})
    elapsed = time.perf_counter() - started
    recall = 100.0 * stats['covered'] / stats['must']
    precision = 100.0 * stats['spans_true'] / stats['spans_counted'] if stats['spans_counted'] else 100.0
    return {
        'recall_pct': round(recall, 2), 'precision_pct': round(precision, 2),
        'must_mask_values': stats['must'], 'covered': stats['covered'],
        'spans_counted': stats['spans_counted'], 'spans_true': stats['spans_true'],
        'spans_excluded_inside_neutral': stats['excluded_neutral'],
        'must_pass_words_masked': stats['must_pass_masked'],
        'recall_by_category': {k: {**v, 'recall_pct': round(100.0 * v['covered'] / v['must'], 2)} for k, v in sorted(by_category.items())},
        'sanity_violations': sanity, 'missed': missed, 'false_positives': false_positives,
    }, elapsed


def verdict(a, b):
    recall_gain = round(b['recall_pct'] - a['recall_pct'], 2)
    precision_change = round(b['precision_pct'] - a['precision_pct'], 2)
    adopt = recall_gain >= ADOPT_MIN_RECALL_GAIN_POINTS and -precision_change <= ADOPT_MAX_PRECISION_DROP_POINTS
    return {'verdict': 'ADOPT' if adopt else "DON'T ADOPT", 'recall_gain_points': recall_gain,
            'precision_change_points': precision_change,
            'rule': f'adopt if recall gain >= {ADOPT_MIN_RECALL_GAIN_POINTS} points AND precision drop <= {ADOPT_MAX_PRECISION_DROP_POINTS} points'}


def markdown(report):
    a, b, v = report['setups']['A'], report['setups']['B'], report['verdict']
    lines = [f"# Privacy filter comparison ({report['date']})", '',
             f"**Verdict: {v['verdict']}.** {v['rule']}. Recall change {v['recall_gain_points']:+} points, precision change {v['precision_change_points']:+} points.", '',
             f"Test set: {report['snippets']} invented snippets ({report['must_mask_values']} must-mask values). Terms list: eval-only, {report['eval_terms']} entries.", '',
             '| Setup | Recall % | Precision % | Covered / must-mask | True spans / counted spans | Run time (s, not scored) |', '|---|---|---|---|---|---|']
    for key in ('A', 'B'):
        s = report['setups'][key]
        lines.append(f"| {key} ({SETUPS[key]}) | {s['recall_pct']} | {s['precision_pct']} | {s['covered']} / {s['must_mask_values']} | "
                     f"{s['spans_true']} / {s['spans_counted']} | {report['run_seconds'][key]} |")
    lines += ['', '## Recall by category', '', '| Category | A | B |', '|---|---|---|']
    for category in a['recall_by_category']:
        ca, cb = a['recall_by_category'][category], b['recall_by_category'][category]
        lines.append(f"| {category} ({ca['must']}) | {ca['recall_pct']} | {cb['recall_pct']} |")
    for key in ('A', 'B'):
        s = report['setups'][key]
        lines += ['', f"## Setup {key} ({SETUPS[key]})", '',
                  f"Sanity check (covered value still verbatim in masked output): {len(s['sanity_violations'])} violations. "
                  f"Must-pass words masked: {s['must_pass_words_masked']}. Spans excluded as inside neutral: {s['spans_excluded_inside_neutral']}.", '',
                  f"### Missed values ({len(s['missed'])})", '']
        lines += [f"- {m['snippet']} [{m['category']}] `{m['value']}`" for m in s['missed']] or ['- none']
        lines += ['', f"### False positives ({len(s['false_positives'])})", '']
        lines += [f"- {f['snippet']} [{f['kind']}{', must-pass word' if f['must_pass_word'] else ''}] `{f['value']}`" for f in s['false_positives']] or ['- none']
        if s['sanity_violations']:
            lines += ['', '### Sanity violations', ''] + [f"- {x['snippet']} `{x['value']}`" for x in s['sanity_violations']]
    return '\n'.join(lines) + '\n'


def main(place_names='mask_all'):
    snippets = load_testset()
    terms = parse_terms(generate_testset.EVAL_TERMS.read_text(encoding='utf-8'))
    privacy = PrivacyEngine(place_names=place_names)  # real Presidio/spaCy; the layered model loads on first layered use
    results, seconds = {}, {}
    for key, mode in SETUPS.items():
        privacy.mode = mode
        results[key], elapsed = evaluate(privacy, snippets, terms)
        seconds[key] = round(elapsed, 1)
        print(f"Setup {key} ({mode}): recall {results[key]['recall_pct']}%, precision {results[key]['precision_pct']}%, {seconds[key]} s")
    report = {'date': date.today().isoformat(), 'place_names': place_names, 'snippets': len(snippets), 'eval_terms': len(terms),
              'must_mask_values': results['A']['must_mask_values'],
              'adoption_rule': {'min_recall_gain_points': ADOPT_MIN_RECALL_GAIN_POINTS, 'max_precision_drop_points': ADOPT_MAX_PRECISION_DROP_POINTS},
              'setups': results, 'verdict': verdict(results['A'], results['B']), 'run_seconds': seconds}
    RESULTS.mkdir(exist_ok=True)
    suffix = '' if place_names == 'mask_all' else f'_places-{place_names}'
    stem = RESULTS / f"{report['date']}_comparison{suffix}"
    stem.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    stem.with_suffix('.md').write_text(markdown(report), encoding='utf-8')
    print(f"Verdict: {report['verdict']['verdict']} (recall {report['verdict']['recall_gain_points']:+}, precision {report['verdict']['precision_change_points']:+})")
    print(f'Wrote {stem.with_suffix(".md")}')


if __name__ == '__main__':
    # Default mask_all keeps the original comparison reproducible; pass common_public to see the shipped default.
    main(sys.argv[1] if len(sys.argv) > 1 else 'mask_all')
