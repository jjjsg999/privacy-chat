"""Real-model check of the placeholder instruction (prompts/placeholders.md). Paid: run only with approval.

    .\\.venv\\Scripts\\python.exe -m eval.run_placeholder_check            # dry run: shows the six prompts
    .\\.venv\\Scripts\\python.exe -m eval.run_placeholder_check --live     # 6 replies, capped at US$0.05
    ... --live --model provider/model --cap 0.05

Six invented, already-masked prompts (both original examples among them, without web search) go to the model with the
same system prompt a web chat uses. Each reply is printed in full for you to read, with simple string checks.
Pass: no reply calls a word a placeholder or token, and none asks the user for a real name, date or value.
"""
import argparse
import asyncio
import os
import re
import time

import dev_budget
import engine
import storage
from eval.run_search_planner_check import dev_ceiling

PROMPTS = [
    ('example1_no_search', 'conduct a web search on what happened to PERSON__1 this week'),
    ('example2_no_search', 'conduct a web search about the latest on MAS regulation about ORGANIZATION__1'),
    ('draft_email', 'Draft a short, friendly email to PERSON__1 at CLIENT__2 confirming our meeting next Tuesday.'),
    ('unknown_company', "What do you know about CLIENT__1's latest results?"),
    ('summarise', 'Summarise in one line: PERSON__1 told PERSON__2 that ORGANIZATION__3 will refinance its facility next quarter.'),
    ('judgement', 'Is PERSON__1 a good fit for the CFO role at CLIENT__2? They led treasury at a regional bank for eight years.'),
]
CALLS_IT_A_PLACEHOLDER = re.compile(r'placeholder|\btokens?\b|stand-?in|redact|anonymi[sz]|\bmasked\b|variable', re.I)
ASKS_FOR_REAL_VALUES = re.compile(r"(provide|share|tell me|let me know|clarify|confirm|give me|specify)[^.?!\n]{0,80}"
                                  r"\b(real|actual|full|correct|specific)?\s*(name|date|value|identity|company|organi[sz]ation|person)\b", re.I)


def system_prompt():
    return 'You are a helpful personal assistant.\n\n' + engine.placeholder_prompt(web=True)


def check(reply):
    problems = []
    if CALLS_IT_A_PLACEHOLDER.search(reply):
        problems.append('mentions placeholders/tokens')
    if ASKS_FOR_REAL_VALUES.search(reply):
        problems.append('asks for a real value')
    return problems


async def live(model, cap):
    catalogue = await engine.model_catalogue()
    pricing = next(m['pricing'] for m in catalogue if m['id'] == model)
    from datetime import datetime, timedelta, timezone
    prices = {model: {'input_per_token': pricing.get('prompt', '0'), 'output_per_token': pricing.get('completion', '0'),
                      'per_request': pricing.get('request', '0'), 'all_charges_known': True,
                      'valid_until': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}}
    os.environ['P1_DEV_LIVE_OPT_IN'] = '1'
    os.environ['P1_DEV_BUDGET_USD'] = str(dev_ceiling(cap))
    rows = []
    with dev_budget.development_mode(prices=prices):
        for name, prompt in PROMPTS:
            started = time.monotonic()
            try:
                reply = await engine.complete([{'role': 'system', 'content': system_prompt()}, {'role': 'user', 'content': prompt}],
                                              model, max_tokens=700, purpose='development')
                rows.append((name, prompt, reply, check(reply), time.monotonic() - started))
            except engine.ChatError as exc:
                rows.append((name, prompt, f'(no reply: {engine.failure_reason(exc)})', ['no reply'], time.monotonic() - started))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', action='store_true', help='make the real (paid) calls')
    parser.add_argument('--model', help='model id; default: the saved chat model')
    parser.add_argument('--cap', type=float, default=0.05, help='spend cap in US$ (at most 0.05)')
    args = parser.parse_args()
    if not 0 < args.cap <= 0.05:
        raise SystemExit('The cap must be more than 0 and at most 0.05.')
    model = args.model or storage.settings()['model']
    if not args.live:
        print(f'Dry run. {len(PROMPTS)} prompts would be sent to {model}, capped at US${args.cap}. Add --live to run.')
        for name, prompt in PROMPTS:
            print(f'- {name}: {prompt}')
        return
    rows = asyncio.run(live(model, args.cap))
    print(f'Model: {model}')
    for name, prompt, reply, problems, seconds in rows:
        print('=' * 80)
        print(f'{name} ({seconds:.1f} s) -> {"OK" if not problems else "PROBLEM: " + ", ".join(problems)}')
        print(f'PROMPT: {prompt}')
        print(f'REPLY:\n{reply.strip()}')
    failed = [name for name, _, _, problems, _ in rows if problems]
    print('=' * 80)
    print(f'{len(rows) - len(failed)}/{len(rows)} replies passed the string checks -> {"PASS" if not failed else "FAIL: " + ", ".join(failed)}')
    raise SystemExit(0 if not failed else 1)


if __name__ == '__main__':
    main()
