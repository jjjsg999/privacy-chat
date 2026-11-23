"""Builds eval/testset.jsonl: invented snippets with labelled values. No real data anywhere.

  .\\.venv\\Scripts\\python.exe -m eval.generate_testset

Also writes eval/eval_terms.txt, the eval-only sensitive-terms list (never the user's real one).
Fixed seed: the same command always writes the same files.
"""
import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20261123
GENERATED_COUNT = 500
TESTSET = HERE / 'testset.jsonl'
FREEFORM = HERE / 'freeform.jsonl'
EVAL_TERMS = HERE / 'eval_terms.txt'

# ---------------------------------------------------------------- word lists (all invented)
FIRST = ['Marcus', 'Priya', 'Daniel', 'Elena', 'Tobias', 'Amara', 'Gregory', 'Sofia', 'Nathan', 'Isabel',
         'Oliver', 'Fatima', 'Henry', 'Chloe', 'Rajesh', 'Hannah', 'Victor', 'Leila', 'Samuel', 'Naomi',
         'Patrick', 'Yusuf', 'Clara', 'Dmitri', 'Rachel', 'Andre', 'Lucas', 'Greta', 'Omar', 'Julia',
         'Felix', 'Nadia', 'Ethan', 'Beatrice', 'Kwame', 'Lydia', 'Stefan', 'Imani', 'Caleb', 'Renata']
LAST = ['Hallworth', 'Okonkwo', 'Ferreira', 'Lindqvist', 'Brennan', 'Castellano', 'Whitcombe', 'Patel',
        'Novak', 'Delacroix', 'Abernathy', 'Kowalski', 'Mbeki', 'Ashdown', 'Fontaine', 'Rasmussen', 'Iyer',
        'Calloway', 'Voss', 'Pemberton', 'Moreau', 'Sandoval', 'Eriksen', 'Haddad', 'Thornbury', 'Kapoor',
        'Lombardi', 'Quinlan', 'Yoder', 'Ellery', 'Sorensen', 'Banerjee', 'Draper', 'Navarro', 'Wexford',
        'Osei', 'Gallagher', 'Winslow', 'Halvorsen', 'Tremaine']
ZH_SURNAMES = [('陳', 'Chen'), ('林', 'Lin'), ('黃', 'Huang'), ('張', 'Chang'), ('李', 'Li'), ('王', 'Wang'),
               ('吳', 'Wu'), ('劉', 'Liu'), ('蔡', 'Tsai'), ('楊', 'Yang'), ('許', 'Hsu'), ('鄭', 'Cheng'),
               ('謝', 'Hsieh'), ('郭', 'Kuo'), ('洪', 'Hung')]
ZH_GIVEN = [('偉', 'Wei'), ('倫', 'Lun'), ('雅', 'Ya'), ('婷', 'Ting'), ('志', 'Chih'), ('明', 'Ming'),
            ('怡', 'Yi'), ('君', 'Chun'), ('家', 'Chia'), ('豪', 'Hao'), ('淑', 'Shu'), ('芬', 'Fen'),
            ('俊', 'Chun'), ('宏', 'Hung'), ('美', 'Mei'), ('玲', 'Ling'), ('建', 'Chien'), ('國', 'Kuo'),
            ('欣', 'Hsin'), ('庭', 'Ting')]
LISTED_COMPANIES = ['Brightvale Holdings', 'Norlight Capital', 'Kestrel Marine Group', 'Altamira Biosystems',
                    'Quillon Partners', 'Harrowgate Logistics', 'Fernwick Energy', 'Stonebridge Analytics',
                    'Meridian Oakes', 'Copperfield Robotics', 'Lumenpath Systems', 'Ostrander Foods',
                    'Valemont Insurance', 'Pinecrest Labs', 'Tidewater Ventures', 'Sablewood Media',
                    'Ironquill Software', 'Greywell Pharma', 'Zenthorpe Industries', 'Marlowe & Finch']
CODENAMES = ['Project Larkspur', 'Project Heron', 'Operation Bluebird', 'Project Tamarack', 'Project Gossamer',
             'Operation Ptarmigan', 'Project Sandpiper', 'Project Quicksilver', 'Operation Harvest Moon',
             'Project Ember', 'Project Dovetail', 'Operation Cormorant', 'Project Halcyon', 'Project Thistle',
             'Operation Mudlark']
UNLISTED_COMPANIES = ['Dunmore Textiles', 'Candlewick Studios', 'Rookhaven Bank', 'Yarrowby Tools',
                      'Elmsworth Press', 'Fallowfield Travel', 'Bexley Cycles', 'Larchmont Dental',
                      'Ashgrove Toys', 'Quarrier Hardware']
STREETS = ['Wexley Lane', 'Marlowe Terrace', 'Alder Court', 'Tanglewood Drive', 'Fenwick Road',
           'Corrigan Street', 'Hollis Avenue', 'Birchmont Way', 'Saltmarsh Close', 'Linden Row',
           'Pembrook Place', 'Ravenscroft Road', 'Kingfisher Walk', 'Orchard Hill Road']
TW_ROADS = ['Anhe Road', 'Zhongshan Road', 'Minsheng Road', 'Fuxing Road', 'Xinyi Road']
EMAIL_DOMAINS = ['quaymail.com', 'fernmail.net', 'plumtree.org', 'oakdesk.io', 'tallpine.co', 'brightmail.net']
COUNTRIES = ['Germany', 'Brazil', 'Japan', 'Kenya', 'Canada', 'Vietnam', 'Chile', 'Norway', 'Egypt',
             'Portugal', 'Mexico', 'Poland', 'India', 'Australia', 'France']
CITIES = ['Paris', 'Tokyo', 'Chicago', 'Singapore', 'London', 'Berlin', 'Sydney', 'Toronto', 'Mumbai',
          'Dubai', 'Taipei', 'Hong Kong', 'Madrid', 'New York', 'Seoul']
NOUNS = ['budget', 'contract', 'roadmap', 'quarter', 'invoice', 'server', 'deadline', 'shipment',
         'warehouse', 'forecast', 'proposal', 'headcount', 'timeline', 'milestone', 'prototype']
PRODUCTS = ['Excel', 'Slack', 'Zoom', 'Salesforce', 'Photoshop', 'WhatsApp', 'Kubernetes', 'Jira',
            'Figma', 'Notion']
DATES = ['March 14', 'next Tuesday', '2026-11-05', 'the 3rd of April', 'Friday', 'January 9', 'end of Q3',
         'tomorrow morning', '12/04/2026', 'last Monday']
URLS = ['https://www.example.com/plans/2026', 'http://www.example.com/deck/7', 'https://docs.example.org/spec',
        'www.example.com/portal']

# slot prefix -> (kind of label, category)
SLOT_KINDS = {
    'P': ('must_mask', 'person'), 'ZC': ('must_mask', 'person'), 'ZR': ('must_mask', 'person'),
    'CO': ('must_mask', 'terms'), 'CN': ('must_mask', 'terms'), 'EM': ('must_mask', 'email'),
    'PH': ('must_mask', 'phone'), 'AC': ('must_mask', 'account'), 'AD': ('must_mask', 'address'),
    'CT': ('must_pass', None), 'CY': ('must_pass', None), 'NN': ('must_pass', None), 'PR': ('must_pass', None),
    'DT': ('neutral', None), 'UR': ('neutral', None), 'UC': ('neutral', None), 'TS': ('neutral', None),
}


def digits(rng, n):
    return ''.join(rng.choice('0123456789') for _ in range(n))


def make_value(prefix, rng):
    if prefix == 'P':
        return f'{rng.choice(FIRST)} {rng.choice(LAST)}'
    if prefix == 'ZC':
        given = rng.sample(ZH_GIVEN, 2)
        return rng.choice(ZH_SURNAMES)[0] + given[0][0] + given[1][0]
    if prefix == 'ZR':
        surname, given = rng.choice(ZH_SURNAMES)[1], rng.sample(ZH_GIVEN, 2)
        name = f'{given[0][1]}-{given[1][1].lower()}'
        return rng.choice([f'{surname} {name}', f'{name} {surname}'])
    if prefix == 'CO':
        return rng.choice(LISTED_COMPANIES)
    if prefix == 'CN':
        return rng.choice(CODENAMES)
    if prefix == 'EM':
        first, last = rng.choice(FIRST).lower(), rng.choice(LAST).lower()
        local = rng.choice([f'{first}.{last}', f'{first[0]}.{last}', f'{first}_{last}', f'{last}{digits(rng, 2)}'])
        return f'{local}@{rng.choice(EMAIL_DOMAINS)}'
    if prefix == 'PH':
        style = rng.randrange(6)
        if style == 0:
            return f'({rng.choice(["212", "415", "646", "312"])}) 555-{digits(rng, 4)}'
        if style == 1:
            return f'+1 {rng.choice(["212", "415", "646"])}-555-{digits(rng, 4)}'
        if style == 2:
            return f'+44 20 7946 {digits(rng, 4)}'
        if style == 3:
            return f'+852 5{digits(rng, 3)} {digits(rng, 4)}'
        if style == 4:
            return f'+886 9{digits(rng, 2)} {digits(rng, 3)} {digits(rng, 3)}'
        return f'09{digits(rng, 2)}-{digits(rng, 3)}-{digits(rng, 3)}'
    if prefix == 'AC':
        style = rng.randrange(4)
        if style == 0:
            return f'{digits(rng, 4)} {digits(rng, 4)} {digits(rng, 4)}'
        if style == 1:
            return digits(rng, 11)
        if style == 2:
            return f'{digits(rng, 4)}-{digits(rng, 3)}-{digits(rng, 4)}'
        return f'{digits(rng, 5)}-{digits(rng, 8)}'
    if prefix == 'AD':
        style = rng.randrange(5)
        if style == 4:
            return f'No. {rng.randint(2, 99)}, Lane {rng.randint(10, 400)}, {rng.choice(TW_ROADS)}'
        street = f'{rng.randint(2, 380)} {rng.choice(STREETS)}'
        if style == 3:
            return f'{street}, Apt {rng.randint(1, 9)}{rng.choice("ABCD")}'
        return street
    if prefix == 'CT':
        return rng.choice(COUNTRIES)
    if prefix == 'CY':
        return rng.choice(CITIES)
    if prefix == 'NN':
        return rng.choice(NOUNS)
    if prefix == 'PR':
        return rng.choice(PRODUCTS)
    if prefix == 'DT':
        return rng.choice(DATES)
    if prefix == 'UR':
        return rng.choice(URLS)
    if prefix == 'UC':
        return rng.choice(UNLISTED_COMPANIES)
    if prefix == 'TS':
        return f'[00:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}]'
    raise ValueError(prefix)


# ---------------------------------------------------------------- templates
MEETING = [
    '{TS} Speaker {N}: So {P1} wants the {NN1} before we talk to {CO1}, and {P2} will chase it.',
    '{TS} Speaker {N}: {CN1} stays confidential. Please do not mention {CN1} to {UC1}.',
    '{TS} Speaker {N}: Ping {P1} on {PH1} if the {PR1} export fails again.',
    '{TS} Speaker {N}: Wire the deposit to account {AC1} and copy {P1} on the confirmation.',
    '{TS} Speaker {N}: {P1} is flying to {CY1} on {DT1}; the {NN1} for {CT1} is still open.',
    '{TS} Speaker {N}: {ZC1} 說下週要跟 {CO1} 開會，{P1} 會一起參加。',
    '{TS} Speaker {N}: Honestly, {CO1} is small next to the {CT1} {NN1}. Ask {ZR1} about it.',
    '{TS} Speaker {N}: Action items: {P1} to review {CN1} by {DT1}. {P2} to update the {NN1} in {PR1}.',
    '{TS} Speaker {N}: The office moved to {AD1}. {P1} has the keys, call {PH1}.',
    '{TS} Speaker {N}: Let us not lose the {NN1}. {ZR1} said the {CT1} team needs it before {DT1}.',
    '{TS} Speaker {N}: Per {P1}, {CO1} and {CO2} both want {CN1} moved to {DT1}.',
    '{TS} Speaker {N}: Okay, so for {CY1} we need the {NN1} and the {NN2}. Nothing else.',
    '{TS} Speaker {N}: I will email {EM1} the notes. {ZC1} asked for them too.',
    '{TS} Speaker {N}: {P1} said the {PR1} licence renewal is due {DT1}. Check with {UC1}.',
    '{TS} Speaker {N}: 今天先看 {NN1}，{ZC1} 和 {ZC2} 負責 {CN1}。',
    '{TS} Speaker {N}: We are visiting {CY1} and {CY2} in {CT1}. Book the {NN1} early.',
    '{TS} Speaker {N}: {P1} from {CO1} dialled in from {CY1}. His number is {PH1}.',
    '{TS} Speaker {N}: The {NN1} is late again. Can {PR1} export it before {DT1}?',
]
EMAILS = [
    'Subject: {CN1} update\n\nHi {P1},\n\nPlease review the attached {NN1} before {DT1}. {CO1} has not replied.\n\nBest,\n{P2}',
    'From: {EM1}\nTo: {EM2}\nSubject: Re: {NN1}\n\nThanks {P1}. See {UR1} for the latest {NN1}.',
    'Dear {P1},\n\nYour parcel will be delivered to {AD1} on {DT1}. Call {PH1} to reschedule.\n\nRegards,\n{UC1} Support',
    'Hi team,\n\nThe {NN1} for {CT1} is ready. Wire the balance to account {AC1} and confirm with {P1}.\n\n{P2}',
    'Subject: Intro\n\n{P1}, meet {P2}. {P2} runs procurement at {CO1} and moved from {CY1} last year. Reach {P2} at {EM1}.\n\nCheers',
    'Subject: Meeting notes\n\n{ZC1} and {ZR1} agreed that {CN1} needs a {NN1} by {DT1}. {PR1} access is required.\n\nThanks,\n{P1}',
    'Hello {P1},\n\nYour invoice from {UC1} is attached. Pay to account {AC1}. Questions: {PH1}.\n\nThank you',
    'Subject: Travel\n\nBooked {P1} on the flight to {CY1}. Hotel is near {AD1}. Reference {UR1}.\n\n-- {P2}',
    'Hi {ZC1},\n\n請把 {NN1} 寄到 {EM1}，並副本給 {ZC2}。{DT1} 之前完成。\n\n謝謝',
    'Subject: Quarterly {NN1}\n\nAll, the {NN1} for {CT1} and {CT2} is in {PR1}. No names, no numbers here. See {UR1}.\n\nThanks',
    'Subject: Fwd: {CO1} contract\n\n{P1} asked that we sign by {DT1}. His assistant, {P2}, is on {PH1}. Our file is {CN1}.\n\nRegards',
    'Hi {P1},\n\nI am working from {AD1} this week. Text {PH1} if the {PR1} link breaks.\n\n{P2}',
    'Subject: Reference for {ZR1}\n\nTo whom it may concern: I worked with {ZR1} in {CY1}. Contact {EM1}.\n\n{P1}',
    'Subject: {NN1} review\n\nTeam, the {NN1} review is on {DT1}. Bring your {PR1} files. We will also cover {CY1} and {CT1}.\n\nThanks',
    'Dear {P1},\n\nYour refund will be paid to account {AC1}. If wrong, write to {EM1}.\n\nKind regards,\n{UC1}',
]
CHATS = [
    '[10:{M}] {P1}: pls send {P2} the {NN1} for {CO1}',
    '[10:{M}] {ZR1}: did {ZC1} get the {NN1}? call me {PH1}',
    '[10:{M}] {P1}: dont share {CN1} with {UC1}',
    '[10:{M}] {P1}: my email is {EM1} if you want the {NN1}',
    'hey is {PR1} down for you too? asking for {P1}',
    '[10:{M}] {P1}: dinner at 6? Im at {AD1}, text {PH1}',
    '[10:{M}] {P1}: paid from account {AC1}, tell {P2}',
    'lol {P1} says the {NN1} is due {DT1}. {CO1} will love that',
    '[10:{M}] {ZC1}: 明天 {CY1} 見，{NN1} 請帶著。',
    'ok so {P1} and {P2} are both in {CY1} on {DT1}. anyone want to join?',
    '[10:{M}] {P1}: reset the password for {EM1} and tell {P2}',
    'pls book {CY1} and {CY2} flights, {CT1} trip is on. see {UR1}',
    '[10:{M}] {ZR1}: {CN1} standup moved. dial {PH1}',
    '{P1}: whats a {NN1} again? is it in {PR1}?',
    '[10:{M}] {P1}: Dads at {AD1} again, can you pick him up',
    'Reminder: {NN1} for {CT1} is due {DT1}. No other news.',
]
STYLES = {'meeting': MEETING, 'email': EMAILS, 'chat': CHATS}


def build_snippet(index, style, template, rng):
    chosen, used, parts, cursor = {}, [], [], 0
    labels = {'must_mask': [], 'must_pass': [], 'neutral': []}
    text = ''
    for match in re.finditer(r'\{(\w+)\}', template):
        text += template[cursor:match.start()]
        cursor = match.end()
        name = match.group(1)
        if name == 'N':
            text += str(rng.randint(1, 6))
            continue
        if name == 'M':
            text += f'{rng.randint(0, 59):02d}'
            continue
        prefix = re.match(r'[A-Z]+', name).group(0)
        if name not in chosen:
            for _ in range(50):
                value = make_value(prefix, rng)
                # Distinct slots get distinct values; none may sit inside another (keeps scoring unambiguous).
                if not any(value in other or other in value for other in used):
                    break
            else:
                raise RuntimeError(f'cannot find unique value for {name}')
            chosen[name] = value
            used.append(value)
        value = chosen[name]
        group, category = SLOT_KINDS[prefix]
        item = {'value': value, 'start': len(text), 'end': len(text) + len(value)}
        if category:
            item['category'] = category
        labels[group].append(item)
        text += value
    text += template[cursor:]
    terms = [(v['value'], 'CLIENT' if v['category'] == 'terms' and v['value'] in LISTED_COMPANIES else 'DEAL')
             for v in labels['must_mask'] if v['category'] == 'terms']
    return {'id': f'gen-{index:03d}', 'style': style, 'text': text, **labels}, terms


def locate(text, value):
    """All verbatim occurrences of value, on word boundaries where the edge is a word character."""
    left = r'(?<!\w)' if re.match(r'[A-Za-z0-9_]', value[0]) else ''
    right = r'(?!\w)' if re.match(r'[A-Za-z0-9_]', value[-1]) else ''
    return [(m.start(), m.end()) for m in re.finditer(left + re.escape(value) + right, text)]


def load_freeform():
    snippets, terms = [], []
    for line in FREEFORM.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out = {'id': row['id'], 'style': row['style'], 'text': row['text'], 'must_mask': [], 'must_pass': [], 'neutral': []}
        for group in ('must_mask', 'must_pass', 'neutral'):
            for entry in row[group]:
                value = entry['value'] if isinstance(entry, dict) else entry
                spots = locate(row['text'], value)
                if not spots:
                    raise SystemExit(f"{row['id']}: labelled value not found verbatim: {value!r}")
                for start, end in spots:
                    item = {'value': value, 'start': start, 'end': end}
                    if group == 'must_mask':
                        item['category'] = entry['category']
                    out[group].append(item)
                if group == 'must_mask' and entry['category'] == 'terms':
                    terms.append((value, entry.get('kind', 'CLIENT')))
        # A short value inside a longer must-mask value (a first name inside a full name) is one value.
        spans = [(m['start'], m['end']) for m in out['must_mask']]
        out['must_mask'] = [m for m in out['must_mask']
                            if not any(s <= m['start'] and m['end'] <= e and (s, e) != (m['start'], m['end']) for s, e in spans)]
        snippets.append(out)
    return snippets, terms


def verify(snippet):
    for group in ('must_mask', 'must_pass', 'neutral'):
        for item in snippet[group]:
            if snippet['text'][item['start']:item['end']] != item['value']:
                raise SystemExit(f"{snippet['id']}: {group} value {item['value']!r} not verbatim at {item['start']}:{item['end']}")


def build():
    rng = random.Random(SEED)
    snippets, terms = [], []
    styles = list(STYLES)
    for index in range(GENERATED_COUNT):
        style = styles[index % 3]
        snippet, snippet_terms = build_snippet(index, style, rng.choice(STYLES[style]), rng)
        snippets.append(snippet)
        terms += snippet_terms
    freeform, freeform_terms = load_freeform()
    snippets += freeform
    terms += freeform_terms
    for snippet in snippets:
        verify(snippet)
    unique_terms = sorted(set(terms))
    return snippets, unique_terms


def main():
    snippets, terms = build()
    with open(TESTSET, 'w', encoding='utf-8', newline='\n') as out:
        for snippet in snippets:
            out.write(json.dumps(snippet, ensure_ascii=False) + '\n')
    # The generator also lists every listed company so unlisted ones stay genuinely off the list.
    lines = ['# Eval-only invented terms. Not the real sensitive_terms.txt.']
    lines += [f'{kind}: {value}' for value, kind in terms]
    EVAL_TERMS.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    counts = {g: sum(len(s[g]) for s in snippets) for g in ('must_mask', 'must_pass', 'neutral')}
    print(f'{len(snippets)} snippets, labels {counts}, {len(terms)} eval terms -> {TESTSET.name}')


if __name__ == '__main__':
    main()
