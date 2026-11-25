"""Web search for web chats: query check, search engines, page reading. No model calls happen here.

The pipeline itself (planner call, budget, session records, answer) lives in engine.py. Logs here name only
counts, kinds, status codes and timings: never a query, a page's text or a real name.
"""
import asyncio
import html
import importlib.util
import ipaddress
import logging
import os
import re
from urllib.parse import urljoin, urlsplit

import httpx

from privacy_engine import BOUND_L, BOUND_R, TOKEN, term_matches, terms_pattern

log = logging.getLogger('privacy_llm.web_search')

ENGINE_LABELS = {'brave': 'Brave Search', 'ddgs': 'ddgs', 'fake': 'Fake search'}
RESULTS_PER_SEARCH = 5
QUERY_MAX = 200


def fake_enabled():
    return os.environ.get('PRIVACY_LLM_FAKE_SEARCH') == '1'


def brave_key():
    return os.environ.get('BRAVE_SEARCH_API_KEY', '').strip()


def engine_status():
    """What Settings shows about each engine. Never the key itself."""
    ddgs = importlib.util.find_spec('ddgs') is not None
    status = {
        'brave': {'label': ENGINE_LABELS['brave'], 'available': bool(brave_key()),
                  'detail': 'key saved' if brave_key() else 'no key'},
        'ddgs': {'label': ENGINE_LABELS['ddgs'], 'available': ddgs,
                 'detail': 'no key' if ddgs else 'not installed (pip install ddgs)'},
    }
    if fake_enabled():
        status['fake'] = {'label': ENGINE_LABELS['fake'], 'available': True, 'detail': 'test mode'}
    return status


def active_engine(options):
    """The engine to use: the fake one whenever PRIVACY_LLM_FAKE_SEARCH=1 (tests, scratch UI checks), else the setting."""
    return 'fake' if fake_enabled() else options.get('engine', 'brave')


# ---------------------------------------------------------------- query check (local, deterministic, no model)

class CheckError(Exception):
    """The query could not be checked safely. Nothing may be searched."""


# Words that do not count as meaningful when deciding whether a Generic query still says enough.
STOPWORDS = frozenset('''a an and are as at be by for from has have how in into is it its of on or over that the their
this to under vs was were what when where which who why will with about after before between does do latest recent news
new any all more most other than then there these those our your my his her them they we you'''.split())

_SYMBOL = r'(?:US\$|S\$|HK\$|A\$|C\$|NZ\$|NT\$|RM|\$|€|£|¥|₹|₩)'
_CODE = (r'(?:SGD|USD|EUR|GBP|HKD|CNY|CNH|RMB|JPY|AUD|CAD|CHF|NZD|INR|KRW|TWD|MYR|IDR|THB|PHP|VND|AED|SAR|'
         r'NOK|SEK|DKK|ZAR|BRL|MXN)')
_NUMBER = r'(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)'
_SCALE = r'(?:\s?(?:thousand|million|billion|trillion|mn|mln|bn|mm|tn|k|m|b))?'
_WORDS = r'(?:dollars?|euros?|pounds?|yen|yuan|renminbi|ringgit|rupees?)'
# A currency amount: symbol or ISO code before or after a number, with separators, decimals and scale words.
# Percentages and basis points (4.5%, 25bp) carry no currency and are never matched.
AMOUNT_RE = re.compile(
    r'(?<![\w.$€£¥])(?:'
    + _SYMBOL + r'\s?' + _NUMBER + _SCALE + r'(?:\s?' + _CODE + r')?'
    + r'|' + _CODE + r'\s?' + _SYMBOL + r'?\s?' + _NUMBER + _SCALE
    + r'|' + _NUMBER + _SCALE + r'\s?(?:' + _CODE + r'|' + _WORDS + r')'
    + r')(?![\w%])', re.I)
YEAR_RE = re.compile(r'(?:19|20)\d\d')
SOURCE_LABELS = {'terms': 'your terms list', 'detector': 'found by the detector', 'amount': 'currency amount',
                 'date': 'date', 'unknown': 'unknown placeholder'}


def _collapse(text):
    return ' '.join(text.split())


def meaningful_words(text):
    return [w for w in re.findall(r'[^\W_]+', text) if w.casefold() not in STOPWORDS and (len(w) > 1 or w.isdigit())]


def _contains(text, value):
    return re.search(BOUND_L + re.escape(value) + BOUND_R, text, re.I) is not None


def check_query(query, token_map, terms, *, dates='remove', terms_names='rerun', allowed=None):
    """Check one planner query. Returns {kind, sent_query, rerun_query, removed, rerun_names, rerun_extras, rerun_withheld}.

    kind: public (nothing removed) | generic (something removed, 2+ meaningful words left) | skipped (too little left).
    sent_query never contains a real value from the token map, a terms-list entry or a currency amount; any doubt
    raises CheckError (fail closed: the caller must not search). `allowed(token, value)` names token-map values that
    are public now (a date while dates pass, a name treated as public) and so may appear as written.
    rerun_query ("Rerun unmasked") restores every placeholder and keeps currency amounts; only terms-list names stay out
    when terms_names is "never". rerun_names lists the restored names; rerun_extras the restored dates and amounts.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > QUERY_MAX:
        raise CheckError('unreadable query')
    if not isinstance(token_map, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in token_map.items()):
        raise CheckError('unreadable token map')
    if dates not in ('remove', 'years') or terms_names not in ('rerun', 'never'):
        raise CheckError('unknown setting')
    removed, names, extras, seen = [], [], [], set()
    sent, rerun, cursor, withheld = [], [], 0, False
    allowed_years = set()
    allowed = allowed or (lambda token, value: False)
    for match in TOKEN.finditer(query):
        sent.append(query[cursor:match.start()])
        rerun.append(query[cursor:match.start()])
        cursor = match.end()
        token = match[0]
        kind = token.rsplit('__', 1)[0]
        value = token_map.get(token)
        if value is None or kind == 'LITERAL' or not value.strip():
            entry = {'display': token, 'token': token, 'kind': kind, 'source': 'unknown'}
        elif kind == 'DATE_TIME':
            if dates == 'years' and YEAR_RE.fullmatch(value.strip()):
                sent.append(value.strip())
                rerun.append(value.strip())
                allowed_years.add(value.strip())
                continue
            entry = {'display': value, 'token': token, 'kind': kind, 'source': 'date'}
            rerun.append(value)
            if token not in {e['token'] for e in extras}:
                extras.append(entry)
        else:
            source = 'terms' if term_matches(value, terms) else 'detector'
            entry = {'display': value, 'token': token, 'kind': kind, 'source': source}
            if source == 'terms' and terms_names == 'never':
                withheld = True
            else:
                rerun.append(value)
                if token not in {n['token'] for n in names}:
                    names.append(entry)
        if token not in seen:
            removed.append(entry)
            seen.add(token)
    sent.append(query[cursor:])
    rerun.append(query[cursor:])
    sent_text = ''.join(sent)
    for amount in AMOUNT_RE.finditer(sent_text):
        entry = {'display': amount[0].strip(), 'token': None, 'kind': 'AMOUNT', 'source': 'amount'}
        removed.append(entry)
        extras.append(entry)
    sent_query = _collapse(AMOUNT_RE.sub(' ', sent_text))  # amounts never leave in an ordinary search
    rerun_query = _collapse(''.join(rerun))                  # "Rerun unmasked" sends them, after the user's click
    # Defence in depth: the planner only sees masked text, so no real value can be in what it wrote. If one is, stop.
    for token, value in token_map.items():
        if token.startswith('LITERAL__') or not value.strip() or value.strip() in allowed_years or allowed(token, value):
            continue
        if _contains(sent_query, value):
            raise CheckError('mismatch')
    if terms and terms_pattern(terms)[0].search(sent_query):
        raise CheckError('mismatch')
    if not removed:
        kind = 'public'
    elif len(meaningful_words(sent_query)) >= 2:
        kind = 'generic'
    else:
        kind = 'skipped'
    if kind == 'public' or not (names or extras) or rerun_query == sent_query:
        rerun_query, names, extras = None, [], []
    return {'kind': kind, 'sent_query': sent_query, 'rerun_query': rerun_query, 'removed': removed,
            'rerun_names': names, 'rerun_extras': extras, 'rerun_withheld': withheld}


# ---------------------------------------------------------------- engines

class SearchError(Exception):
    """A search or page read failed. The message is a short reason (timeout, HTTP code), never provider text."""


_TAG = re.compile(r'<[^>]+>')


def _plain(text):
    return _collapse(html.unescape(_TAG.sub('', str(text or ''))))


def _result(title, url, snippet):
    url = str(url or '').strip()
    if urlsplit(url).scheme not in ('http', 'https'):
        return None
    return {'title': _plain(title)[:300] or url, 'url': url[:2000], 'snippet': _plain(snippet)[:1000]}


# Brave Web Search API. Endpoint, X-Subscription-Token header, q/count parameters and web.results[].title/url/description
# checked against https://api-dashboard.search.brave.com/app/documentation/web-search/get-started (read 2026-11-25).
BRAVE_URL = 'https://api.search.brave.com/res/v1/web/search'
FAKE_LOG = []  # (what, value) for every fake search and page read; tests inspect it


async def _brave(query, count):
    key = brave_key()
    if not key:
        raise SearchError('no Brave key')
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(BRAVE_URL, params={'q': query, 'count': count},
                                        headers={'X-Subscription-Token': key, 'Accept': 'application/json'})
    except httpx.TimeoutException:
        raise SearchError('timeout') from None
    except httpx.HTTPError:
        raise SearchError('network error') from None
    if response.status_code != 200:
        raise SearchError(f'HTTP {response.status_code}')
    try:
        items = response.json().get('web', {}).get('results', [])
        if not isinstance(items, list):
            raise ValueError
    except (ValueError, AttributeError):
        raise SearchError('unreadable reply') from None
    return [r for r in (_result(i.get('title'), i.get('url'), i.get('description')) for i in items if isinstance(i, dict)) if r]


async def _ddgs(query, count):
    try:
        from ddgs import DDGS
    except ImportError:
        raise SearchError('ddgs is not installed') from None

    def run():
        return DDGS().text(query, max_results=count)
    try:
        items = await asyncio.wait_for(asyncio.to_thread(run), timeout=15)
    except asyncio.TimeoutError:
        raise SearchError('timeout') from None
    except Exception:  # a scraping library can fail in many ways; the reason stays generic
        raise SearchError('ddgs failed') from None
    return [r for r in (_result(i.get('title'), i.get('href'), i.get('body')) for i in items or [] if isinstance(i, dict)) if r]


def _fake(query, count):
    if not fake_enabled():
        raise SearchError('fake search is off')
    FAKE_LOG.append(('search', query))
    words = ' '.join(meaningful_words(query)[:4]) or 'query'
    return [{'title': f'Fake result {n} about {words}', 'url': f'https://example.org/fake/{n}',
             'snippet': f'A canned snippet {n} for {words}.'} for n in range(1, count + 1)]


async def search(engine, query, count=RESULTS_PER_SEARCH):
    """[{title, url, snippet}] from one engine. Raises SearchError with a short reason."""
    if engine == 'brave':
        results = await _brave(query, count)
    elif engine == 'ddgs':
        results = await _ddgs(query, count)
    elif engine == 'fake':
        results = _fake(query, count)
    else:
        raise SearchError('unknown engine')
    return results[:count]


# ---------------------------------------------------------------- page reading

TEXT_CAPS = {'lean': 2_000, 'balanced': 6_000, 'full': 12_000}
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
PAGE_TIMEOUT = 10
USER_AGENT = 'Mozilla/5.0 (compatible; PrivacyLLM/1.0; local assistant)'
HTML_TYPES = ('text/html', 'application/xhtml+xml')


def app_ports():
    ports = {8000}
    try:
        ports.add(int(os.environ.get('PRIVACY_LLM_PORT', '8000')))
    except ValueError:
        pass
    return ports


async def resolve_host(host, port):
    """Every IP address `host` resolves to."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=0)
    return sorted({info[4][0] for info in infos})


def address_allowed(ip):
    """Only public unicast addresses: no loopback, private, link-local, multicast, reserved or unspecified."""
    try:
        address = ipaddress.ip_address(ip.split('%', 1)[0])
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not (address.is_loopback or address.is_private or address.is_link_local
                                      or address.is_multicast or address.is_reserved or address.is_unspecified)


async def check_url(url):
    """SSRF guard for one hop. Raises SearchError('blocked address') unless the URL points at a public web host."""
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise SearchError('blocked address')
    host = parts.hostname.rstrip('.').lower()
    try:
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError:
        raise SearchError('blocked address') from None
    if host == 'localhost' or host.endswith('.localhost') or host.endswith('.local') or host.endswith('.internal'):
        raise SearchError('blocked address')
    if port in app_ports() and host in ('127.0.0.1', '::1', '0.0.0.0'):
        raise SearchError('blocked address')
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = await resolve_host(host, port)
        except OSError:
            raise SearchError('host not found') from None
    if not addresses or not all(address_allowed(a) for a in addresses):
        raise SearchError('blocked address')


async def fetch_page(url, client):
    """HTML of one page: http(s) only, every hop checked, at most 3 redirects, 2 MB, text/html only."""
    for hop in range(MAX_REDIRECTS + 1):
        await check_url(url)
        try:
            async with client.stream('GET', url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html'}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    if not location or hop == MAX_REDIRECTS:
                        raise SearchError('too many redirects')
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise SearchError(f'HTTP {response.status_code}')
                kind = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                if kind not in HTML_TYPES:
                    raise SearchError('not a web page')
                declared = response.headers.get('content-length')
                if declared and declared.isdigit() and int(declared) > MAX_PAGE_BYTES:
                    raise SearchError('page too large')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_PAGE_BYTES:
                        raise SearchError('page too large')
                return bytes(body).decode(response.encoding or 'utf-8', errors='replace')
        except httpx.TimeoutException:
            raise SearchError('timeout') from None
        except httpx.HTTPError:
            raise SearchError('network error') from None
    raise SearchError('too many redirects')


def extract_text(page_html):
    """Main text of a page with trafilatura. HTML is stripped; nothing in it is run or followed."""
    import trafilatura
    try:
        text = trafilatura.extract(page_html, include_comments=False, include_tables=True, include_links=False,
                                   include_images=False, favor_precision=True)
    except Exception:  # a malformed page must only cost its text, never the search
        return ''
    return _collapse(text or '')


def new_client():
    # No cookies are kept between requests, no credentials or proxies from the environment, redirects checked by hand.
    return httpx.AsyncClient(timeout=PAGE_TIMEOUT, follow_redirects=False, trust_env=False)


async def read_sources(results, pages, mode, engine):
    """[{title, url, snippet, text}] plus the number of pages read. A page that fails falls back to its snippet."""
    cap = TEXT_CAPS.get(mode, TEXT_CAPS['balanced'])
    sources, read = [], 0
    async with new_client() as client:
        for position, result in enumerate(results):
            text = ''
            if position < pages:
                try:
                    if engine == 'fake':
                        FAKE_LOG.append(('page', result['url']))
                        text = f'Fake page text for "{result["title"]}". ' * 3
                    else:
                        text = extract_text(await fetch_page(result['url'], client))
                except SearchError as exc:
                    log.info('page read failed: %s', exc)
                if text:
                    read += 1
            sources.append({**result, 'text': (text or result['snippet'])[:cap]})
    return sources, read
